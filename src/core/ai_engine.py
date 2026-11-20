"""
Módulo: src/core/ai_engine.py
Proyecto: Diagnóstico Inteligente de Motores DC - Expo ICE Impulsa 2026
Descripción:
    Motor de Inteligencia Artificial ligera (Random Forest + Respaldo Físico RL)
    para clasificar automáticamente la condición operativa del motor DC en los
    5 escenarios obligatorios a partir de sus transitorios eléctricos:
    1. NORMAL
    2. SOBRECARGA
    3. BAJO_VOLTAJE
    4. ROZAMIENTO_MECANICO
    5. BLOQUEO_PARCIAL
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import re
import sys
import unicodedata
import warnings

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import StratifiedKFold, cross_val_score

RAIZ_PROYECTO = Path(__file__).resolve().parents[2]
MODELO_POR_DEFECTO = RAIZ_PROYECTO / "models" / "motor_rf_classifier.joblib"

# Soporte de importación tanto desde la raíz del proyecto como ejecutando el archivo directo
try:
    from src.core.motor_profile import MotorSpecProfile
    from src.core.transient_processor import (
        TransientAnalysisResult,
        TransientProcessor,
        configurar_salida_utf8,
        integrar_trapecio,
        resolver_csv_por_defecto,
    )
except ImportError:
    sys.path.append(str(RAIZ_PROYECTO))
    from src.core.motor_profile import MotorSpecProfile
    from src.core.transient_processor import (
        TransientAnalysisResult,
        TransientProcessor,
        configurar_salida_utf8,
        integrar_trapecio,
        resolver_csv_por_defecto,
    )

# ==============================================================================
# DEFINICIÓN DE CLASES CANÓNICAS Y VECTOR DE CARACTERÍSTICAS
# ==============================================================================
CANONICAL_STATES: List[str] = [
    "NORMAL",
    "SOBRECARGA",
    "BAJO_VOLTAJE",
    "ROZAMIENTO_MECANICO",
    "BLOQUEO_PARCIAL",
]

FEATURE_COLUMNS: List[str] = [
    "v_ss_v",
    "i_pico_suave_a",
    "i_ss_a",
    "t_pico_ms",
    "t_estab_s",
    "tau_ms",
    "p_max_w",
    "p_ss_w",
    "e_arranque_j",
    "ratio_pico_ss",
    "caida_transitoria_pct",
    "r_devanado_est_ohm",
    "r_aparente_ss_ohm",
    "factor_fcem",
    "norm_v_ratio",
    "norm_iss_ratio",
    "norm_ipico_ratio",
    "norm_testab_ratio",
    "norm_tau_ratio",
    "norm_energia_ratio",
]

# Versión del formato del archivo .joblib (2 = incluye baseline)
FORMATO_MODELO_VERSION = 2


# ==============================================================================
# NORMALIZACIÓN DE ETIQUETAS (palabras completas, sin falsos positivos por subcadena)
# ==============================================================================
_TOKENS_FALLA: Dict[str, frozenset] = {
    "BLOQUEO_PARCIAL": frozenset({
        "BLOQUEO", "BLOQUEADO", "PARCIAL", "STALL", "STALLED", "ATASCO", "ATASCADO", "TRABADO",
    }),
    "ROZAMIENTO_MECANICO": frozenset({
        "ROZAMIENTO", "ROCE", "FRICCION", "FRICTION", "EJE",
    }),
    "SOBRECARGA": frozenset({
        "SOBRECARGA", "SOBRECARGADO", "CARGA", "INERCIA", "VENTILADOR", "POLEA",
    }),
}
_TOKENS_NORMAL = frozenset({"NORMAL", "SANO", "NUEVO", "BASE", "BASELINE", "NOMINAL"})
_TOKENS_BAJO = frozenset({"BAJO", "BAJA", "UNDERVOLTAGE"})
_TOKENS_VOLTAJE = frozenset({"VOLTAJE", "TENSION"})
_TOKENS_SUBVOLTAJE = frozenset({"SUBTENSION", "SUBVOLTAJE"})
_VOLTAJE_REDUCIDO_MAX_V = 10.5


def _quitar_acentos(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def _es_voltaje_reducido(token: str) -> bool:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)V", token)
    return bool(m) and float(m.group(1)) <= _VOLTAJE_REDUCIDO_MAX_V


def normalizar_etiqueta_estado(raw_label: str) -> str:
    """
    Mapea etiquetas libres de CSVs ('Motor_Nuevo', 'Con_Friccion', 'ESC4', etc.) a las
    5 clases oficiales, o a 'DESCONOCIDO'.
    """
    texto = _quitar_acentos(str(raw_label)).strip().upper()
    texto = re.sub(r"[^A-Z0-9.]+", "_", texto).strip("_")

    if texto in CANONICAL_STATES:
        return texto

    m_esc = re.search(r"(?:^|_)ESC(?:ENARIO)?_?([1-5])(?:_|$)", texto)
    if m_esc:
        return CANONICAL_STATES[int(m_esc.group(1)) - 1]

    tokens = {t for t in texto.split("_") if t}

    candidatas = {estado for estado, claves in _TOKENS_FALLA.items() if tokens & claves}

    hay_bajo = bool(tokens & _TOKENS_BAJO)
    if (
        (hay_bajo and tokens & _TOKENS_VOLTAJE)
        or tokens & _TOKENS_SUBVOLTAJE
        or any(_es_voltaje_reducido(t) for t in tokens)
    ):
        candidatas.add("BAJO_VOLTAJE")
    elif hay_bajo and not candidatas:
        candidatas.add("BAJO_VOLTAJE")

    if len(candidatas) == 1:
        return next(iter(candidatas))
    if len(candidatas) > 1:
        return "DESCONOCIDO"
    if tokens & _TOKENS_NORMAL:
        return "NORMAL"
    return "DESCONOCIDO"


@dataclass
class DiagnosticReport:
    """Resultado técnico estructurado listo para interfaz CAD, exportación y reporte."""
    archivo: str
    estado_detectado: str
    confianza_pct: float
    severidad: str  # "OK", "ADVERTENCIA", "CRITICO"
    metodo_inferencia: str
    parametros_clave: Dict[str, Any]
    sintomas_detectados: List[str]
    recomendacion_tecnica: str
    probabilidades_clase: Dict[str, float] = field(default_factory=dict)
    perfil_motor: Optional[str] = None
    id_motor: Optional[int] = None

    def formatear_reporte_texto(self) -> str:
        """Genera el bloque de salida técnica alineado con el formato esperado de Expo ICE 2026."""
        t_est_s = self.parametros_clave.get("t_estab_s", 0.0)
        v_ss = self.parametros_clave.get("v_ss_v", 0.0)
        i_pico = self.parametros_clave.get("i_pico_suave_a", 0.0)
        i_ss = self.parametros_clave.get("i_ss_a", 0.0)
        tau_ms = self.parametros_clave.get("tau_ms", 0.0)
        e_j = self.parametros_clave.get("e_arranque_j", 0.0)
        p_max = self.parametros_clave.get("p_max_w", 0.0)

        linea_perfil = (
            f"Perfil de motor : {self.perfil_motor}\n"
            if self.perfil_motor
            else ""
        )
        sintomas_str = "\n".join(f"  - {s}" for s in self.sintomas_detectados)
        return (
            f"Estado detectado: {self.estado_detectado} (Confianza: {self.confianza_pct:.1f}%)\n"
            f"Severidad       : {self.severidad} | Motor IA: {self.metodo_inferencia}\n"
            f"{linea_perfil}"
            f"────────────────────────────────────────────────────────\n"
            f"Voltaje estable         = {v_ss:.2f} V\n"
            f"Corriente pico          = {i_pico:.2f} A ({i_pico*1000:.0f} mA)\n"
            f"Corriente estable (Iss) = {i_ss:.2f} A ({i_ss*1000:.0f} mA)\n"
            f"Tiempo de estabilización= {t_est_s:.3f} s ({t_est_s*1000:.0f} ms)\n"
            f"Constante de tiempo (τ) = {tau_ms:.2f} ms\n"
            f"Potencia máxima         = {p_max:.2f} W\n"
            f"Energía de arranque     = {e_j:.3f} J\n"
            f"────────────────────────────────────────────────────────\n"
            f"Diagnóstico físico:\n{sintomas_str}\n"
            f"Acción recomendada: {self.recomendacion_tecnica}"
        )


class MotorDiagnosticAI:
    """
    Motor de diagnóstico inteligente ligero (Random Forest + Reglas Físicas RL
    + Calibración opcional por Perfil Nominal de Placa / SQLite).
    """

    def __init__(self, model_path: str | Path = MODELO_POR_DEFECTO):
        self.model_path = Path(model_path)
        self.classifier: Optional[RandomForestClassifier] = None
        self.baseline_result: Optional[TransientAnalysisResult] = None
        self.motor_profile: Optional[MotorSpecProfile] = None
        self.training_metrics: Dict[str, Any] = {}

        if self.model_path.exists():
            self.load_model(self.model_path)

    def set_baseline(self, baseline: TransientAnalysisResult) -> None:
        """
        Define la curva base de referencia (Escenario 1: Operación Normal).
        Si ya hay un clasificador entrenado, reentrena después de cambiarla.
        """
        self.baseline_result = baseline

    def set_motor_profile(self, profile: Optional[MotorSpecProfile]) -> None:
        """Asigna especificaciones nominales opcionales (manuales o desde SQLite)."""
        self.motor_profile = profile

    # ==========================================================================
    # PERSISTENCIA DEL MODELO (clasificador + baseline)
    # ==========================================================================
    def save_model(self, path: Optional[str | Path] = None) -> Path:
        if self.classifier is None:
            raise RuntimeError("No hay ningún clasificador entrenado para guardar.")

        target = Path(path) if path else self.model_path
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": FORMATO_MODELO_VERSION,
            "classifier": self.classifier,
            "feature_columns": list(FEATURE_COLUMNS),
            "training_metrics": self.training_metrics,
            "baseline": self.baseline_result.to_baseline_dict() if self.baseline_result is not None else None,
        }
        joblib.dump(payload, target)
        return target

    def load_model(self, path: Optional[str | Path] = None) -> bool:
        """
        Carga clasificador + baseline. Devuelve False (sin lanzar excepción) si el archivo
        no existe, está dañado, es de formato antiguo o fue entrenado con otras features.
        """
        target = Path(path) if path else self.model_path
        if not target.exists():
            return False

        try:
            payload = joblib.load(target)
        except Exception as exc:
            warnings.warn(
                f"No se pudo leer el modelo '{target.name}' ({exc}). Se usa el motor físico de respaldo.",
                RuntimeWarning,
                stacklevel=2,
            )
            return False

        if not isinstance(payload, dict) or payload.get("classifier") is None:
            warnings.warn(
                f"El archivo '{target.name}' no contiene un clasificador válido.",
                RuntimeWarning,
                stacklevel=2,
            )
            return False

        if payload.get("feature_columns") != FEATURE_COLUMNS:
            warnings.warn(
                f"El modelo '{target.name}' fue entrenado con otras FEATURE_COLUMNS. "
                "Reentrena el modelo; se usa el motor físico de respaldo.",
                RuntimeWarning,
                stacklevel=2,
            )
            return False

        if int(payload.get("version", 1)) < FORMATO_MODELO_VERSION:
            warnings.warn(
                f"El modelo '{target.name}' es de formato antiguo y no guarda la curva base: "
                "sus predicciones serían incorrectas. Reentrena el modelo; "
                "se usa el motor físico de respaldo.",
                RuntimeWarning,
                stacklevel=2,
            )
            return False

        baseline = None
        datos_baseline = payload.get("baseline")
        if datos_baseline:
            try:
                baseline = TransientAnalysisResult.from_baseline_dict(datos_baseline)
            except (ValueError, TypeError) as exc:
                warnings.warn(
                    f"Curva base del modelo '{target.name}' inválida ({exc}). Reentrena el modelo.",
                    RuntimeWarning,
                    stacklevel=2,
                )
                return False

        self.classifier = payload["classifier"]
        self.baseline_result = baseline
        self.training_metrics = payload.get("training_metrics", {})
        return True

    # ==========================================================================
    # ENTRENAMIENTO CON DATASETS REALES Y BOOTSTRAP FÍSICO DE 1ER ORDEN
    # ==========================================================================
    def train_from_results(
        self,
        results: List[TransientAnalysisResult],
        save_after_train: bool = True,
    ) -> Dict[str, Any]:
        """
        Entrena el clasificador Random Forest ligero a partir de una lista de
        resultados procesados de CSVs reales etiquetados.
        """
        if self.baseline_result is None:
            normales = [r for r in results if normalizar_etiqueta_estado(r.label) == "NORMAL"]
            if normales:
                self.baseline_result = normales[0]

        rows: List[Dict[str, Any]] = []
        omitidos: List[str] = []
        for res in results:
            clase = normalizar_etiqueta_estado(res.label)
            if clase == "DESCONOCIDO":
                omitidos.append(f"{res.source_name} [{res.label}]")
                continue
            feat = res.to_feature_dict(baseline=self.baseline_result)
            feat["target"] = clase
            rows.append(feat)

        if omitidos:
            warnings.warn(
                f"{len(omitidos)} archivo(s) omitido(s) por etiqueta no reconocida: "
                + "; ".join(omitidos),
                RuntimeWarning,
                stacklevel=2,
            )

        df_train = pd.DataFrame(rows)
        if df_train.empty or df_train["target"].nunique() < 2:
            raise ValueError(
                "Se requieren al menos 2 clases distintas etiquetadas (ej. NORMAL y SOBRECARGA) "
                "para entrenar el modelo supervisado. Usa `bootstrap_from_baseline()` si aún "
                "solo cuentas con la curva base del Escenario 1."
            )

        X = df_train[FEATURE_COLUMNS].astype(float)
        y = df_train["target"].astype(str)

        clf = RandomForestClassifier(
            n_estimators=60,
            max_depth=6,
            min_samples_leaf=1,
            class_weight="balanced",
            random_state=42,
        )

        min_class_count = int(y.value_counts().min())
        if min_class_count >= 3:
            n_splits = min(5, min_class_count)
            cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
            cv_scores = cross_val_score(clf, X, y, cv=cv)
            cv_acc = float(np.mean(cv_scores) * 100.0)
        else:
            cv_acc = float("nan")

        clf.fit(X, y)
        self.classifier = clf

        importances = dict(
            sorted(
                zip(FEATURE_COLUMNS, np.round(clf.feature_importances_, 4)),
                key=lambda item: item[1],
                reverse=True,
            )
        )
        self.training_metrics = {
            "origen": "DATASETS_REALES_CSV",
            "total_muestras": int(len(df_train)),
            "archivos_omitidos": len(omitidos),
            "distribucion_clases": y.value_counts().to_dict(),
            "cv_accuracy_pct": round(cv_acc, 2) if not np.isnan(cv_acc) else "N/A (<3 muestras/clase)",
            "top_features": dict(list(importances.items())[:6]),
        }

        if save_after_train:
            self.save_model()

        return self.training_metrics

    def bootstrap_from_baseline(
        self,
        baseline: TransientAnalysisResult,
        samples_per_class: int = 30,
        save_after_train: bool = True,
    ) -> Dict[str, Any]:
        """
        Genera un entrenamiento sintético calibrado físicamente usando el modelo
        RL dinámico de 1er orden (V_th = (Rw + Rm(t))*i + L*di/dt) a partir de una
        sola curva base real (Escenario 1).
        """
        self.set_baseline(baseline)
        rng = np.random.default_rng(42)

        v_nom = max(baseline.v_ss_v, 5.0)
        i_pico_base = max(baseline.i_pico_suave_ma / 1000.0, 0.2)
        i_ss_base = max(baseline.i_ss_ma / 1000.0, 0.05)
        tau_base_s = max(baseline.tau_ms / 1000.0, 0.03)

        # Estimación física de Rw y Rm_inf del motor real bajo prueba (o tomada de placa)
        if (
            self.motor_profile is not None
            and self.motor_profile.rw_ohm is not None
            and self.motor_profile.rw_ohm > 0
        ):
            rw_est = float(self.motor_profile.rw_ohm)
        else:
            rw_est = float(np.clip(v_nom / (i_pico_base * 1.15), 1.0, 15.0))

        r_total_ss = v_nom / i_ss_base
        r_minf_est = max(2.0, r_total_ss - rw_est)

        if (
            self.motor_profile is not None
            and self.motor_profile.l_mh is not None
            and self.motor_profile.l_mh > 0
        ):
            L_est = float(self.motor_profile.l_mh) / 1000.0
        else:
            L_est = float(np.clip(rw_est * 0.008, 0.005, 0.08))

        def _simular_curva_rl(
            v_th: float, rw: float, L: float, r_minf: float, tau_m: float, clase: str
        ) -> Dict[str, Any]:
            dt = 0.0005
            dur = max(1.8, 4.5 * tau_m)
            t = np.arange(0.0, dur, dt)
            i = np.zeros_like(t)
            curr = 0.0
            tau_m_safe = max(tau_m, 1e-4)
            L_safe = max(L, 1e-5)

            # Paso exponencial exacto local: incondicionalmente estable para cualquier R, L y dt
            for k in range(1, len(t)):
                rm = r_minf * (1.0 - np.exp(-t[k] / tau_m_safe)) ** 2
                r_eq = max(rw + rm, 1e-4)
                decay = float(np.exp(-(r_eq / L_safe) * dt))
                curr = curr * decay + (v_th / r_eq) * (1.0 - decay)
                i[k] = curr + rng.normal(0.0, 0.004 * i_ss_base)
            i = np.maximum(0.0, i)

            idx_p = int(np.argmax(i))
            i_p = float(i[idx_p])
            t_p_ms = float(t[idx_p] * 1000.0)
            i_ss = float(np.mean(i[int(0.75 * len(i)):]))
            v_ss = float(v_th + rng.normal(0.0, 0.04))

            tol = max(0.01, 0.05 * i_ss)
            fuera = np.where(np.abs(i - i_ss) > tol)[0]
            t_est_s = float(t[fuera[-1] + 1]) if (len(fuera) > 0 and fuera[-1] + 1 < len(t)) else float(t[-1])

            if i_p > i_ss * 1.02:
                i_tau_obj = i_p - 0.632 * (i_p - i_ss)
                post = np.where(i[idx_p:] <= i_tau_obj)[0]
                tau_ms = float((t[idx_p + post[0]] - t[idx_p]) * 1000.0) if len(post) > 0 else 5.0
            else:
                i_tau_obj = i_p
                tau_ms = 0.0

            p = v_ss * i
            p_max = float(np.max(p))
            p_ss = float(np.mean(p[int(0.75 * len(i)):]))
            e_arr = integrar_trapecio(p[t <= t_est_s], t[t <= t_est_s])

            fake_res = TransientAnalysisResult(
                source_name="sintetico_rl.csv",
                label=clase,
                t_raw=t,
                v_raw=np.full_like(t, v_ss),
                i_raw_ma=i * 1000.0,
                t_uni=t,
                v_smooth=np.full_like(t, v_ss),
                i_smooth_ma=i * 1000.0,
                p_inst_w=p,
                i_pico_crudo_ma=i_p * 1000.0,
                i_pico_suave_ma=i_p * 1000.0,
                t_pico_ms=t_p_ms,
                i_ss_ma=i_ss * 1000.0,
                v_ss_v=v_ss,
                t_estab_ms=t_est_s * 1000.0,
                t_corte_ms=dur * 1000.0,
                tau_ms=tau_ms,
                t_tau_s=tau_ms / 1000.0,
                i_tau_obj_ma=i_tau_obj * 1000.0,
                p_max_w=p_max,
                p_ss_w=p_ss,
                e_arranque_j=e_arr,
                e_total_j=e_arr,
                fs_real_hz=2000.0,
                dt_med_us=500.0,
                n_pre_samples=0,
                n_active_samples=len(t),
            )
            feat = fake_res.to_feature_dict(baseline=baseline)
            feat["target"] = clase
            return feat

        rows: List[Dict[str, Any]] = []
        real_row = baseline.to_feature_dict(baseline=baseline)
        real_row["target"] = "NORMAL"
        rows.append(real_row)

        for _ in range(samples_per_class):
            # 1. NORMAL
            rows.append(_simular_curva_rl(
                v_nom * rng.uniform(0.96, 1.04), rw_est * rng.uniform(0.95, 1.05), L_est,
                r_minf_est * rng.uniform(0.90, 1.12), tau_base_s * rng.uniform(0.85, 1.15), "NORMAL"
            ))
            # 2. SOBRECARGA
            rows.append(_simular_curva_rl(
                v_nom * rng.uniform(0.94, 1.01), rw_est, L_est,
                r_minf_est * rng.uniform(0.35, 0.62), tau_base_s * rng.uniform(1.65, 2.80), "SOBRECARGA"
            ))
            # 3. BAJO_VOLTAJE
            rows.append(_simular_curva_rl(
                v_nom * rng.uniform(0.50, 0.82), rw_est, L_est,
                r_minf_est * rng.uniform(0.90, 1.10), tau_base_s * rng.uniform(0.95, 1.40), "BAJO_VOLTAJE"
            ))
            # 4. ROZAMIENTO_MECANICO
            rows.append(_simular_curva_rl(
                v_nom * rng.uniform(0.95, 1.02), rw_est, L_est,
                r_minf_est * rng.uniform(0.45, 0.72), tau_base_s * rng.uniform(1.05, 1.55), "ROZAMIENTO_MECANICO"
            ))
            # 5. BLOQUEO_PARCIAL
            rows.append(_simular_curva_rl(
                v_nom * rng.uniform(0.90, 1.00), rw_est, L_est,
                r_minf_est * rng.uniform(0.00, 0.18), tau_base_s * rng.uniform(0.40, 1.10), "BLOQUEO_PARCIAL"
            ))

        df_syn = pd.DataFrame(rows)
        X = df_syn[FEATURE_COLUMNS].astype(float)
        y = df_syn["target"].astype(str)

        clf = RandomForestClassifier(n_estimators=60, max_depth=6, random_state=42)
        cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
        cv_scores = cross_val_score(clf, X, y, cv=cv)
        clf.fit(X, y)
        self.classifier = clf

        self.training_metrics = {
            "origen": f"BOOTSTRAP_FISICO_RL (Base: {baseline.source_name})",
            "rw_estimado_ohm": round(rw_est, 3),
            "r_minf_estimado_ohm": round(r_minf_est, 3),
            "total_muestras": int(len(df_syn)),
            "muestras_reales": 1,
            "muestras_sinteticas": int(len(df_syn)) - 1,
            "cv_consistencia_sintetica_pct": round(float(np.mean(cv_scores) * 100.0), 2),
            "advertencia": (
                "Consistencia con el simulador RL, NO precisión real. "
                "Validar con CSVs reales de los 5 escenarios (train_from_results)."
            ),
        }
        if save_after_train:
            self.save_model()
        return self.training_metrics

    # ==========================================================================
    # INFERENCIA, CALIBRACIÓN POR PLACA Y EXPLICABILIDAD FÍSICA
    # ==========================================================================
    def _evaluar_reglas_fisicas(self, feat: Dict[str, Any]) -> Tuple[str, float, Dict[str, float]]:
        """Clasificador determinístico de respaldo basado en invariantes RL y desviaciones."""
        scores = {c: 0.05 for c in CANONICAL_STATES}

        ratio_p_ss = feat["ratio_pico_ss"]
        caida_pct = feat["caida_transitoria_pct"]
        norm_v = feat["norm_v_ratio"]
        norm_iss = feat["norm_iss_ratio"]
        norm_testab = feat["norm_testab_ratio"]
        norm_tau = feat["norm_tau_ratio"]

        # Si no hay curva base pero sí perfil nominal del motor, evitar referencia fija a 12 V
        if self.baseline_result is None and self.motor_profile is not None:
            v_ref = self.motor_profile.voltaje_referencia()
            if v_ref is not None and v_ref > 0:
                norm_v = feat["v_ss_v"] / v_ref
            if self.motor_profile.i_nominal_a is not None and self.motor_profile.i_nominal_a > 0:
                norm_iss = feat["i_ss_a"] / float(self.motor_profile.i_nominal_a)

        # 1. Bloqueo parcial / rotor estancado (caída Inrush mínima, FCEM casi nula)
        if ratio_p_ss < 1.45 or caida_pct < 28.0:
            scores["BLOQUEO_PARCIAL"] += 0.85

        # 2. Bajo voltaje (< 84% del voltaje base/nominal)
        if norm_v < 0.84:
            scores["BAJO_VOLTAJE"] += 0.80

        # 3. Sobrecarga vs Rozamiento Mecánico
        if norm_iss > 1.25:
            if norm_testab > 1.35 or norm_tau > 1.35:
                scores["SOBRECARGA"] += 0.75
            else:
                scores["ROZAMIENTO_MECANICO"] += 0.72
        elif ratio_p_ss < 1.95 and norm_v >= 0.85:
            if norm_tau > 1.30:
                scores["SOBRECARGA"] += 0.65
            else:
                scores["ROZAMIENTO_MECANICO"] += 0.65

        # 4. Operación Normal
        if 0.85 <= norm_v <= 1.15 and norm_iss <= 1.25 and ratio_p_ss >= 1.95:
            scores["NORMAL"] += 0.85

        total = sum(scores.values())
        probs = {k: round(v / total, 4) for k, v in scores.items()}
        best_state = max(probs, key=probs.get)
        return best_state, probs[best_state] * 100.0, probs

    def _calibrar_con_perfil_motor(
        self,
        result: TransientAnalysisResult,
        probs: Dict[str, float],
        hallazgos: List[str],
    ) -> Tuple[Dict[str, float], List[str]]:
        """
        Ajusta las probabilidades del clasificador utilizando los parámetros
        nominales proporcionados (rango de voltaje, Rw, Inom, Istall).
        """
        profile = self.motor_profile
        if profile is None or not profile.tiene_datos():
            hallazgos.append(
                "Perfil nominal no proporcionado: diagnóstico inferido íntegramente desde la curva transitoria."
            )
            return probs, hallazgos

        p_adj = dict(probs)
        v_ss = float(result.v_ss_v)
        i_ss_a = float(result.i_ss_ma) / 1000.0
        i_pico_a = float(result.i_pico_suave_ma) / 1000.0

        # A. Validación contra rango de voltaje nominal del motor
        v_ref = profile.voltaje_referencia()
        if v_ref is not None and v_ref > 0:
            v_min = profile.v_nom_min if (profile.v_nom_min is not None and profile.v_nom_min > 0) else 0.88 * v_ref
            v_max = profile.v_nom_max if (profile.v_nom_max is not None and profile.v_nom_max > 0) else 1.12 * v_ref
            if v_ss < 0.94 * v_min:
                deficit = min(1.0, (v_min - v_ss) / max(v_min, 0.1))
                p_adj["BAJO_VOLTAJE"] = p_adj.get("BAJO_VOLTAJE", 0.0) + 1.5 * (0.4 + deficit)
                hallazgos.append(
                    f"[Placa] Subtensión confirmada: V_medido={v_ss:.2f} V < rango nominal ({v_min:.1f}–{v_max:.1f} V)."
                )
            elif v_min * 0.94 <= v_ss <= v_max * 1.05:
                p_adj["BAJO_VOLTAJE"] *= 0.15
                p_adj["NORMAL"] = p_adj.get("NORMAL", 0.0) + 0.25
                hallazgos.append(
                    f"[Placa] Tensión V_medido={v_ss:.2f} V dentro del rango nominal ({v_min:.1f}–{v_max:.1f} V)."
                )
            else:
                hallazgos.append(
                    f"[Placa] Advertencia de sobretensión: V_medido={v_ss:.2f} V > máximo nominal ({v_max:.1f} V)."
                )

        # B. Validación contra resistencia interna Rw (separación de FCEM vs Bloqueo)
        if profile.rw_ohm is not None and profile.rw_ohm > 0 and i_ss_a > 1e-4:
            rw = float(profile.rw_ohm)
            r_app = v_ss / i_ss_a
            ratio_r = r_app / rw
            if ratio_r <= 1.35:
                p_adj["BLOQUEO_PARCIAL"] = p_adj.get("BLOQUEO_PARCIAL", 0.0) + 1.8
                p_adj["NORMAL"] *= 0.10
                hallazgos.append(
                    f"[Placa] R_aparente={r_app:.2f} Ω ≈ Rw_placa={rw:.2f} Ω ({ratio_r:.2f}x): ausencia de FCEM (bloqueo de eje)."
                )
            elif ratio_r <= 1.85:
                p_adj["SOBRECARGA"] = p_adj.get("SOBRECARGA", 0.0) + 0.55
                p_adj["ROZAMIENTO_MECANICO"] = p_adj.get("ROZAMIENTO_MECANICO", 0.0) + 0.55
                hallazgos.append(
                    f"[Placa] R_aparente={r_app:.2f} Ω moderada frente a Rw_placa={rw:.2f} Ω ({ratio_r:.2f}x): pérdida de FCEM."
                )
            else:
                p_adj["NORMAL"] = p_adj.get("NORMAL", 0.0) + 0.25
                hallazgos.append(
                    f"[Placa] Desarrollo de FCEM saludable: R_aparente={r_app:.2f} Ω ({ratio_r:.2f}x sobre Rw={rw:.2f} Ω)."
                )

            if i_pico_a > 1e-4:
                rw_arranque = v_ss / i_pico_a
                if rw_arranque > 1.45 * rw:
                    hallazgos.append(
                        f"[Placa] Nota: Rw aparente en pico ({rw_arranque:.2f} Ω) > Rw nominal ({rw:.2f} Ω); revisar escobillas/contactos."
                    )

        # C. Validación contra corriente nominal y corriente de rotor bloqueado
        if profile.i_nominal_a is not None and profile.i_nominal_a > 0:
            inom = float(profile.i_nominal_a)
            if i_ss_a > 1.35 * inom:
                p_adj["SOBRECARGA"] = p_adj.get("SOBRECARGA", 0.0) + 0.75
                p_adj["ROZAMIENTO_MECANICO"] = p_adj.get("ROZAMIENTO_MECANICO", 0.0) + 0.45
                p_adj["NORMAL"] *= 0.25
                hallazgos.append(
                    f"[Placa] Sobrecorriente estable: Iss={i_ss_a:.2f} A excede I_nominal={inom:.2f} A (+{(i_ss_a/inom - 1)*100:.0f}%)."
                )

        if profile.i_stall_a is not None and profile.i_stall_a > 0:
            istall = float(profile.i_stall_a)
            if i_ss_a >= 0.70 * istall:
                p_adj["BLOQUEO_PARCIAL"] = p_adj.get("BLOQUEO_PARCIAL", 0.0) + 1.6
                p_adj["NORMAL"] *= 0.10
                hallazgos.append(
                    f"[Placa] Iss={i_ss_a:.2f} A cercana a I_stall={istall:.2f} A: condición crítica de rotor bloqueado."
                )

        total = sum(max(0.0, val) for val in p_adj.values())
        if total > 0:
            p_adj = {k: round(max(0.0, val) / total, 4) for k, val in p_adj.items()}
        return p_adj, hallazgos

    def _construir_explicacion_tecnica(
        self, estado: str, feat: Dict[str, Any]
    ) -> Tuple[str, List[str], str]:
        """Traduce las métricas numéricas en síntomas físicos y recomendaciones técnicas."""
        sintomas: List[str] = []
        v_ss = feat["v_ss_v"]
        i_pico = feat["i_pico_suave_a"]
        i_ss = feat["i_ss_a"]
        t_est = feat["t_estab_s"]
        caida = feat["caida_transitoria_pct"]
        norm_iss = feat["norm_iss_ratio"]
        norm_v = feat["norm_v_ratio"]

        if estado == "NORMAL":
            severidad = "OK"
            sintomas.append(f"Relación Inrush/Estable saludable ({feat['ratio_pico_ss']:.2f}x; caída del {caida:.1f}%).")
            sintomas.append(f"Tensión de alimentación estable en {v_ss:.2f} V y estabilización en {t_est:.3f} s.")
            recomendacion = "Motor en condiciones nominales. Mantener plan rutinario de monitoreo."

        elif estado == "SOBRECARGA":
            severidad = "ADVERTENCIA"
            if self.baseline_result is not None:
                sintomas.append(f"Incremento de corriente estable (+{(norm_iss - 1)*100:.1f}% respecto a base).")
            sintomas.append(f"Corriente permanente elevada ({i_ss:.2f} A) y tiempo de estabilización prolongado ({t_est:.3f} s).")
            sintomas.append(f"Mayor demanda energética en arranque ({feat['e_arranque_j']:.2f} J) por alto par/inercia.")
            recomendacion = "Verificar carga mecánica acoplada (ventilador, polea o transmisión) y reducir par resistente."

        elif estado == "BAJO_VOLTAJE":
            severidad = "ADVERTENCIA"
            sintomas.append(f"Tensión de alimentación reducida ({v_ss:.2f} V; {norm_v*100:.1f}% del valor nominal).")
            sintomas.append(f"Limitación del pico de arranque a {i_pico:.2f} A con pérdida de par electromagnético.")
            recomendacion = "Revisar fuente de alimentación, estado de batería, reguladores o caídas de tensión en cableado."

        elif estado == "ROZAMIENTO_MECANICO":
            severidad = "ADVERTENCIA"
            sintomas.append(f"Incremento de corriente nominal de operación ({i_ss:.2f} A) con compresión de relación pico/estable ({feat['ratio_pico_ss']:.2f}x).")
            sintomas.append(f"Fricción parásita reduce la Fuerza Contraelectromotriz efectiva (factor FCEM = {feat['factor_fcem']:.2f}).")
            recomendacion = "Se recomienda inspección mecánica de rodamientos, bujes, lubricación y alineación del eje."

        else:  # BLOQUEO_PARCIAL
            severidad = "CRITICO"
            sintomas.append(f"Caída transitoria mínima tras el arranque ({caida:.1f}%); la corriente permanece cerca del límite Inrush ({i_ss:.2f} A vs {i_pico:.2f} A).")
            sintomas.append(f"Resistencia dinámica mecánica casi nula (R_aparente = {feat['r_aparente_ss_ohm']:.2f} Ω); riesgo severo de sobrecalentamiento por efecto Joule.")
            recomendacion = "DETENER MOTOR INMEDIATAMENTE. Verificar obstrucción física o atascamiento del rotor antes de reenergizar."

        return severidad, sintomas, recomendacion

    def diagnose(self, result: TransientAnalysisResult) -> DiagnosticReport:
        """Ejecuta el diagnóstico completo sobre un transitorio procesado."""
        feat = result.to_feature_dict(baseline=self.baseline_result)

        if self.classifier is not None:
            X_vec = pd.DataFrame([feat])[FEATURE_COLUMNS].astype(float)
            proba_arr = self.classifier.predict_proba(X_vec)[0]
            classes = list(self.classifier.classes_)
            probs = {c: 0.0 for c in CANONICAL_STATES}
            for cls_name, p_val in zip(classes, proba_arr):
                probs[cls_name] = round(float(p_val), 4)
            metodo = "IA_RANDOM_FOREST"
        else:
            _, _, probs = self._evaluar_reglas_fisicas(feat)
            metodo = "MOTOR_FISICO_RL"

        hallazgos_placa: List[str] = []
        probs, hallazgos_placa = self._calibrar_con_perfil_motor(
            result, probs, hallazgos_placa
        )

        perfil_resumen: Optional[str] = None
        id_motor: Optional[int] = None
        if self.motor_profile is not None and self.motor_profile.tiene_datos():
            metodo = f"{metodo}+PLACA"
            perfil_resumen = self.motor_profile.resumen_corto()
            id_motor = self.motor_profile.id_motor

        estado = max(probs, key=probs.get)
        confianza = probs[estado] * 100.0

        severidad, sintomas, recomendacion = self._construir_explicacion_tecnica(estado, feat)
        sintomas.extend(hallazgos_placa)

        if severidad == "OK" and any("Advertencia" in h for h in hallazgos_placa):
            severidad = "ADVERTENCIA"

        return DiagnosticReport(
            archivo=result.source_name,
            estado_detectado=estado,
            confianza_pct=round(confianza, 2),
            severidad=severidad,
            metodo_inferencia=metodo,
            parametros_clave=feat,
            sintomas_detectados=sintomas,
            recomendacion_tecnica=recomendacion,
            probabilidades_clase=probs,
            perfil_motor=perfil_resumen,
            id_motor=id_motor,
        )


if __name__ == "__main__":
    configurar_salida_utf8()

    ruta_csv = resolver_csv_por_defecto(sys.argv)

    procesador = TransientProcessor()
    registro_base = procesador.analyze_csv(ruta_csv)

    ia = MotorDiagnosticAI()
    metricas_train = ia.bootstrap_from_baseline(registro_base, samples_per_class=30, save_after_train=True)

    print("\n=== ENTRENAMIENTO / CALIBRACIÓN DE IA LIGERA ===")
    for k, v in metricas_train.items():
        print(f" {k:<30}: {v}")

    reporte = ia.diagnose(registro_base)
    print("\n=== RESULTADO DEL DIAGNÓSTICO AUTOMÁTICO ===")
    print(reporte.formatear_reporte_texto())