"""
Módulo: src/core/transient_processor.py
Proyecto: Diagnóstico Inteligente de Motores DC - Expo ICE Impulsa 2026
Descripción:
    Procesamiento numérico, extracción de parámetros físicos y graficación
    de transitorios eléctricos a partir de archivos CSV generados por
    recolector_pru_2_ver2.py (Captura SRAM 4 kHz + 100 ms pre-trigger).
"""

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
import re
import sys

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


# ==============================================================================
# PARÁMETROS ESTÁNDAR DE PROCESAMIENTO (ALINEADOS CON recolector_pru_2_ver2.py)
# ==============================================================================
UMBRAL_ARRANQUE_MA = 5.0       # Umbral de disparo (3 muestras consecutivas > 5.0 mA)
TIEMPO_PRE_S = 0.100           # 100 ms previos al disparo (usados para offset, no graficados)
VENTANA_ESTABILIDAD_S = 0.18   # Ventana móvil de evaluación de estabilidad (180 ms)
DT_UNIFORME = 0.00025          # Malla uniforme de 0.25 ms (4,000 Hz)
SIGMA_CORRIENTE_MS = 3.0       # Suavizado fino para conservar el pico Inrush real
SIGMA_VOLTAJE_MS = 8.0         # Suavizado de bus de tensión
SIGMA_ENVOLVENTE_MS = 35.0     # Envolvente lenta para detección de estabilización

RAIZ_PROYECTO = Path(__file__).resolve().parents[2]
NOMBRE_CSV_PRUEBA = "dataset_Motor_prueba_12V.csv"

# Campos de TransientAnalysisResult que son vectores (no se guardan en el baseline)
_CAMPOS_VECTORIALES = (
    "t_raw", "v_raw", "i_raw_ma",
    "t_uni", "v_smooth", "i_smooth_ma", "p_inst_w",
)


# ==============================================================================
# UTILIDADES GENERALES
# ==============================================================================
def integrar_trapecio(y: np.ndarray, x: np.ndarray) -> float:
    """Integral trapezoidal compatible con NumPy 2.x (trapezoid) y 1.x (trapz)."""
    funcion = getattr(np, "trapezoid", None) or getattr(np, "trapz")
    return float(funcion(y, x))


def configurar_salida_utf8() -> None:
    """
    Fuerza UTF-8 en stdout/stderr para evitar UnicodeEncodeError en consolas
    de Windows con cp1252. Llamar solo desde bloques `if __name__ == "__main__"`.
    """
    for flujo in (sys.stdout, sys.stderr):
        reconfigurar = getattr(flujo, "reconfigure", None)
        if callable(reconfigurar):
            try:
                reconfigurar(encoding="utf-8", errors="replace")
            except Exception:
                pass


def resolver_csv_por_defecto(argv: Optional[Sequence[str]] = None) -> Path:
    """
    Devuelve la ruta del CSV de prueba. Prioridad:
      1. Argumento de línea de comandos (argv[1]).
      2. <raíz del proyecto>/datasets/raw/dataset_Motor_prueba_12V.csv
      3. datasets/raw/... y archivo suelto en el directorio de trabajo actual.
      4. Archivo suelto junto a este módulo.
    """
    if argv is not None and len(argv) > 1:
        candidatos = [Path(argv[1])]
    else:
        candidatos = [
            RAIZ_PROYECTO / "datasets" / "raw" / NOMBRE_CSV_PRUEBA,
            Path("datasets/raw") / NOMBRE_CSV_PRUEBA,
            Path(NOMBRE_CSV_PRUEBA),
            Path(__file__).resolve().parent / NOMBRE_CSV_PRUEBA,
        ]

    for ruta in candidatos:
        if ruta.exists():
            return ruta

    probadas = "\n".join(f"  - {c.resolve()}" for c in candidatos)
    raise FileNotFoundError(
        "No se encontró el CSV de prueba. Rutas probadas:\n"
        f"{probadas}\n"
        "Pasa la ruta como argumento o colócalo en datasets/raw/."
    )


@dataclass
class TransientAnalysisResult:
    """Contenedor completo de señales procesadas, marcadores visuales y parámetros."""
    source_name: str
    label: str

    # Vectores crudos recortados (t >= 0 hasta t_fin_transitorio)
    t_raw: np.ndarray          # [s]
    v_raw: np.ndarray          # [V]
    i_raw_ma: np.ndarray       # [mA]

    # Vectores suavizados en malla uniforme (0.25 ms)
    t_uni: np.ndarray          # [s]
    v_smooth: np.ndarray       # [V]
    i_smooth_ma: np.ndarray    # [mA]
    p_inst_w: np.ndarray       # [W] Potencia instantánea V(t) * I(t)

    # Parámetros principales del transitorio (para UI, reporte e IA)
    i_pico_crudo_ma: float
    i_pico_suave_ma: float
    t_pico_ms: float
    i_ss_ma: float
    v_ss_v: float
    t_estab_ms: Optional[float]
    t_corte_ms: float
    tau_ms: float
    t_tau_s: float
    i_tau_obj_ma: float
    p_max_w: float
    p_ss_w: float
    e_arranque_j: float
    e_total_j: float

    # Calidad de muestreo del archivo
    fs_real_hz: float
    dt_med_us: float
    n_pre_samples: int
    n_active_samples: int
    metadata: Dict[str, Any] = field(default_factory=dict)

    # --------------------------------------------------------------------------
    # Serialización ligera de la curva base (sin vectores) para guardarla con el modelo
    # --------------------------------------------------------------------------
    def to_baseline_dict(self) -> Dict[str, Any]:
        """
        Devuelve solo los parámetros escalares del transitorio (sin vectores de señal).
        Es todo lo que `to_feature_dict(baseline=...)` necesita de una curva base.
        """
        datos: Dict[str, Any] = {}
        for campo in fields(self):
            if campo.name in _CAMPOS_VECTORIALES:
                continue
            valor = getattr(self, campo.name)
            if isinstance(valor, np.generic):
                valor = valor.item()
            elif isinstance(valor, dict):
                valor = dict(valor)
            datos[campo.name] = valor
        return datos

    @classmethod
    def from_baseline_dict(cls, datos: Dict[str, Any]) -> "TransientAnalysisResult":
        """
        Reconstruye una curva base mínima (vectores vacíos) a partir de `to_baseline_dict()`.
        Sirve para normalizar features; no se puede graficar.
        """
        if not isinstance(datos, dict):
            raise TypeError("El baseline debe ser un diccionario.")

        vacio = np.empty(0, dtype=float)
        kwargs: Dict[str, Any] = {}
        faltantes: List[str] = []

        for campo in fields(cls):
            if campo.name in _CAMPOS_VECTORIALES:
                kwargs[campo.name] = vacio.copy()
            elif campo.name in datos:
                kwargs[campo.name] = datos[campo.name]
            elif campo.name == "metadata":
                continue  # usa default_factory
            else:
                faltantes.append(campo.name)

        if faltantes:
            raise ValueError(f"Baseline incompleto, faltan campos: {', '.join(faltantes)}")

        resultado = cls(**kwargs)
        resultado.metadata = {**resultado.metadata, "reconstruido_desde_baseline": True}
        return resultado

    def to_feature_dict(
        self, baseline: Optional["TransientAnalysisResult"] = None
    ) -> Dict[str, Any]:
        """
        Vector completo de 21 características numéricas (20 usadas por el clasificador)
        + metadatos para IA ligera. Si se proporciona `baseline` (Escenario 1), calcula
        las desviaciones relativas respecto al estado sano del motor.
        """
        t_estab_s = (
            (self.t_estab_ms / 1000.0)
            if self.t_estab_ms is not None
            else (self.t_corte_ms / 1000.0)
        )
        i_pico_a = max(self.i_pico_suave_ma / 1000.0, 1e-4)
        i_ss_a = max(self.i_ss_ma / 1000.0, 1e-4)
        tau_ms_safe = max(self.tau_ms, 1.0)
        e_arr_safe = max(self.e_arranque_j, 1e-5)

        # Invariantes físicos adimensionales (generales para cualquier motor DC)
        ratio_pico_ss = i_pico_a / i_ss_a
        caida_transitoria_pct = ((i_pico_a - i_ss_a) / i_pico_a) * 100.0
        r_devanado_est_ohm = self.v_ss_v / i_pico_a
        r_aparente_ss_ohm = self.v_ss_v / i_ss_a
        factor_fcem = float(np.clip(1.0 - (i_ss_a / i_pico_a), 0.0, 1.0))

        # Razones normalizadas respecto a curva base (Escenario 1)
        if baseline is not None:
            b_v = max(baseline.v_ss_v, 0.1)
            b_iss = max(baseline.i_ss_ma / 1000.0, 1e-4)
            b_ipico = max(baseline.i_pico_suave_ma / 1000.0, 1e-4)
            b_testab = max(
                (baseline.t_estab_ms if baseline.t_estab_ms is not None else baseline.t_corte_ms) / 1000.0,
                1e-3,
            )
            b_tau = max(baseline.tau_ms, 1.0)
            b_e = max(baseline.e_arranque_j, 1e-5)

            norm_v_ratio = self.v_ss_v / b_v
            norm_iss_ratio = i_ss_a / b_iss
            norm_ipico_ratio = i_pico_a / b_ipico
            norm_testab_ratio = t_estab_s / b_testab
            norm_tau_ratio = tau_ms_safe / b_tau
            norm_energia_ratio = e_arr_safe / b_e
        else:
            # Sin curva base explícita: referencia nominal de 12 V y razones neutras
            norm_v_ratio = self.v_ss_v / 12.0
            norm_iss_ratio = 1.0
            norm_ipico_ratio = 1.0
            norm_testab_ratio = 1.0
            norm_tau_ratio = 1.0
            norm_energia_ratio = 1.0

        return {
            "archivo": self.source_name,
            "estado_etiqueta": self.label,
            # 1. Variables eléctricas y temporales directas
            "v_ss_v": round(self.v_ss_v, 3),
            "i_pico_crudo_a": round(self.i_pico_crudo_ma / 1000.0, 4),
            "i_pico_suave_a": round(i_pico_a, 4),
            "i_ss_a": round(i_ss_a, 4),
            "t_pico_ms": round(self.t_pico_ms, 2),
            "t_estab_s": round(t_estab_s, 4),
            "tau_ms": round(tau_ms_safe, 2),
            "p_max_w": round(self.p_max_w, 3),
            "p_ss_w": round(self.p_ss_w, 3),
            "e_arranque_j": round(self.e_arranque_j, 4),
            # 2. Invariantes físicos del modelo RL dinámico (Rw vs Rm_inf)
            "ratio_pico_ss": round(ratio_pico_ss, 4),
            "caida_transitoria_pct": round(caida_transitoria_pct, 2),
            "r_devanado_est_ohm": round(r_devanado_est_ohm, 3),
            "r_aparente_ss_ohm": round(r_aparente_ss_ohm, 3),
            "factor_fcem": round(factor_fcem, 4),
            # 3. Desviaciones normalizadas contra Escenario 1 (Curva Base)
            "norm_v_ratio": round(norm_v_ratio, 4),
            "norm_iss_ratio": round(norm_iss_ratio, 4),
            "norm_ipico_ratio": round(norm_ipico_ratio, 4),
            "norm_testab_ratio": round(norm_testab_ratio, 4),
            "norm_tau_ratio": round(norm_tau_ratio, 4),
            "norm_energia_ratio": round(norm_energia_ratio, 4),
        }


def procesar_senal_numerica(
    t_raw: np.ndarray,
    y_raw: np.ndarray,
    dt_uniforme: float = DT_UNIFORME,
    sigma_ms: float = 3.0,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Interpolación uniforme (4 kHz) + filtro mediana de 3 puntos + suavizado Gaussiano.
    Conserva el algoritmo de recolector_pru_2_ver2.py.
    """
    t_mono = np.array(t_raw, dtype=float)
    for k in range(1, len(t_mono)):
        if t_mono[k] <= t_mono[k - 1]:
            t_mono[k] = t_mono[k - 1] + 1e-6

    duracion = float(t_mono[-1])
    if duracion <= dt_uniforme or len(t_mono) < 3:
        return t_mono, np.array(y_raw, dtype=float)

    t_uni = np.arange(0.0, duracion, dt_uniforme)
    y_uni = np.interp(t_uni, t_mono, y_raw)

    # Filtro de mediana de 3 muestras para eliminar impulsos espurios de conmutación
    if len(y_uni) >= 5:
        y_med = y_uni.copy()
        y_med[1:-1] = np.median(np.vstack([y_uni[:-2], y_uni[1:-1], y_uni[2:]]), axis=0)
        y_uni = y_med

    # Convolución Gaussiana
    sigma_muestras = max(1.0, (sigma_ms / 1000.0) / dt_uniforme)
    radio = int(np.ceil(3 * sigma_muestras))
    if len(y_uni) > 2 * radio + 1:
        x_k = np.arange(-radio, radio + 1)
        kernel = np.exp(-0.5 * (x_k / sigma_muestras) ** 2)
        kernel /= np.sum(kernel)
        y_pad = np.pad(y_uni, (radio, radio), mode="edge")
        return t_uni, np.convolve(y_pad, kernel, mode="valid")

    return t_uni, y_uni


class TransientProcessor:
    """Procesador offline de datasets CSV para diagnóstico de motores DC."""

    def __init__(
        self,
        dt_uniforme: float = DT_UNIFORME,
        sigma_corriente_ms: float = SIGMA_CORRIENTE_MS,
        sigma_voltaje_ms: float = SIGMA_VOLTAJE_MS,
        ventana_estabilidad_s: float = VENTANA_ESTABILIDAD_S,
        restar_offset_basal: bool = True,
    ):
        self.dt_uniforme = dt_uniforme
        self.sigma_corriente_ms = sigma_corriente_ms
        self.sigma_voltaje_ms = sigma_voltaje_ms
        self.ventana_estabilidad_s = ventana_estabilidad_s
        self.restar_offset_basal = restar_offset_basal

    def analyze_csv(self, filepath: str | Path) -> TransientAnalysisResult:
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"No se encontró el archivo CSV: {path}")

        df = pd.read_csv(path)
        cols_map = {c.strip().lower(): c.strip() for c in df.columns}
        df = df.rename(columns={orig: low for low, orig in cols_map.items()})

        # 1. Identificación de columnas (formato SRAM nuevo vs Timestamp legado)
        if "tiempo(s)" in df.columns:
            t_col = pd.to_numeric(df["tiempo(s)"], errors="coerce").values
            ya_disparado = True
        elif "timestamp" in df.columns:
            t_abs = pd.to_numeric(df["timestamp"], errors="coerce").values
            t_col = t_abs - t_abs[0] if len(t_abs) > 0 else t_abs
            ya_disparado = False
        elif "tiempo" in df.columns:
            t_col = pd.to_numeric(df["tiempo"], errors="coerce").values
            ya_disparado = bool(np.nanmin(t_col) < 0.0) if len(t_col) > 0 else False
        else:
            raise ValueError(f"'{path.name}' no contiene columna 'Tiempo(s)' ni 'Timestamp'.")

        v_col = next((c for c in df.columns if c.startswith(("voltaje", "tension", "v_"))), None)
        i_col = next((c for c in df.columns if c.startswith(("corriente", "i_"))), None)
        if not v_col or not i_col:
            raise ValueError(f"'{path.name}' debe contener columnas de Voltaje y Corriente.")

        v_arr = pd.to_numeric(df[v_col], errors="coerce").values
        i_raw_col = pd.to_numeric(df[i_col], errors="coerce").values

        # Filtrar filas corruptas / NaN / Inf para evitar contaminación numérica
        valid_mask = np.isfinite(t_col) & np.isfinite(v_arr) & np.isfinite(i_raw_col)
        if not np.all(valid_mask):
            t_col = t_col[valid_mask]
            v_arr = v_arr[valid_mask]
            i_raw_col = i_raw_col[valid_mask]

        if len(t_col) < 5:
            raise ValueError(f"'{path.name}' no contiene suficientes muestras numéricas válidas.")

        # Normalizar corriente internamente a mA (detecta (mA), _ma, [mA], (A), _a, [A])
        tiene_unidad_ma = bool(re.search(r"(?:^|[^a-z])ma(?:[^a-z]|$)", i_col))
        tiene_unidad_a = bool(re.search(r"(?:^|[^a-z])a(?:[^a-z]|$)", i_col))
        if tiene_unidad_ma:
            es_amperes = False
        elif tiene_unidad_a:
            es_amperes = True
        else:
            es_amperes = float(np.max(np.abs(i_raw_col))) <= 25.0

        i_ma_arr = (i_raw_col * 1000.0) if es_amperes else i_raw_col.copy()

        label = "Sin_Etiqueta"
        if "estado" in df.columns:
            estados_validos = df["estado"].dropna()
            if len(estados_validos) > 0:
                label = str(estados_validos.iloc[0]).strip() or "Sin_Etiqueta"

        # 2. Separación de ventana pre-trigger (-100 ms) y anclaje estricto en t = 0.0 s
        if ya_disparado and np.min(t_col) < 0.0:
            pre_mask = t_col < 0.0
            n_pre = int(np.sum(pre_mask))
            v_off = float(np.median(v_arr[pre_mask])) if (self.restar_offset_basal and n_pre >= 3) else 0.0
            i_off = float(np.median(i_ma_arr[pre_mask])) if (self.restar_offset_basal and n_pre >= 3) else 0.0

            post_mask = t_col >= 0.0
            t_seg = t_col[post_mask]
            v_seg = np.maximum(0.0, v_arr[post_mask] - v_off)
            i_seg = i_ma_arr[post_mask] - i_off
        else:
            # Búsqueda de 3 muestras consecutivas > 5.0 mA
            encendido = i_ma_arr > UMBRAL_ARRANQUE_MA
            disparos = np.where(encendido[:-2] & encendido[1:-1] & encendido[2:])[0]
            idx_cero = max(0, int(disparos[0]) - 1) if len(disparos) > 0 else 0
            n_pre = idx_cero

            v_off = float(np.median(v_arr[:idx_cero])) if (self.restar_offset_basal and idx_cero >= 5) else 0.0
            i_off = float(np.median(i_ma_arr[:idx_cero])) if (self.restar_offset_basal and idx_cero >= 5) else 0.0

            t_arranque = float(t_col[idx_cero])
            t_seg = t_col[idx_cero:] - t_arranque
            v_seg = np.maximum(0.0, v_arr[idx_cero:] - v_off)
            i_seg = i_ma_arr[idx_cero:] - i_off

        if len(t_seg) < 5:
            raise ValueError(f"Datos insuficientes tras el disparo en '{path.name}'.")

        # 3. Descartar tramo apagado si se cortó el motor dentro del registro
        v_ref_on = float(np.max(v_seg[:min(len(v_seg), 500)]))
        apagado = np.where((t_seg > 0.25) & (v_seg < 0.70 * v_ref_on))[0]
        if len(apagado) > 0 and int(apagado[0]) >= 10:
            idx_fin_on = int(apagado[0])
            t_seg = t_seg[:idx_fin_on]
            v_seg = v_seg[:idx_fin_on]
            i_seg = i_seg[:idx_fin_on]

        # 4. Procesamiento numérico (malla uniforme 4 kHz + mediana + Gaussiano)
        t_uni, v_smooth = procesar_senal_numerica(t_seg, v_seg, self.dt_uniforme, self.sigma_voltaje_ms)
        _, i_smooth = procesar_senal_numerica(t_seg, i_seg, self.dt_uniforme, self.sigma_corriente_ms)
        _, i_env = procesar_senal_numerica(t_seg, i_seg, self.dt_uniforme, SIGMA_ENVOLVENTE_MS)
        _, v_env = procesar_senal_numerica(t_seg, v_seg, self.dt_uniforme, SIGMA_ENVOLVENTE_MS)

        # 5. Detector adaptativo de estabilización
        lim_busqueda = min(len(i_smooth), max(int(0.5 / self.dt_uniforme), int(len(i_smooth) * 0.5)))
        idx_pico = int(np.argmax(i_smooth[:lim_busqueda]))
        t_pico = float(t_uni[idx_pico])
        i_max = float(i_smooth[idx_pico])

        win_s = self.ventana_estabilidad_s
        win_n = max(10, int(win_s / self.dt_uniforme))
        t_fin_transitorio = float(t_uni[-1])
        t_estab_detectado: Optional[float] = None
        tol_banda_v = max(0.08, 0.01 * v_ref_on)

        for k in range(idx_pico + int(0.06 / self.dt_uniforme), len(t_uni) - win_n):
            if v_env[k + win_n - 1] < 0.70 * np.max(v_env[:k + win_n]):
                t_fin_transitorio = float(t_uni[k])
                break

            seg_i = i_env[k:k + win_n]
            seg_v = v_env[k:k + win_n]

            caida = i_max - float(np.mean(seg_i))
            banda_i = float(np.max(seg_i) - np.min(seg_i))
            pendiente_i = abs(float(seg_i[-1] - seg_i[0])) / win_s
            banda_v = float(np.max(seg_v) - np.min(seg_v))

            if (
                caida >= max(3.0, 0.20 * i_max)
                and banda_i < max(2.5, 0.035 * caida)
                and pendiente_i < 25.0
                and banda_v < tol_banda_v
            ):
                t_estab_detectado = float(t_uni[k + win_n // 2])
                margen = max(0.12, 0.20 * t_estab_detectado)
                t_fin_transitorio = min(float(t_uni[-1]), t_estab_detectado + margen)
                break

        # 6. Recorte de vectores finales para graficación y extracción de parámetros
        mask_uni = t_uni <= t_fin_transitorio
        t_plot = t_uni[mask_uni]
        v_plot = v_smooth[mask_uni]
        i_plot = i_smooth[mask_uni]

        mask_raw = t_seg <= t_fin_transitorio
        t_raw_plot = t_seg[mask_raw]
        v_raw_plot = v_seg[mask_raw]
        i_raw_plot = i_seg[mask_raw]

        i_max_crudo = float(np.max(i_raw_plot))
        mask_ss = t_plot >= (t_plot[-1] * 0.75)
        i_ss = float(np.mean(i_plot[mask_ss]))
        v_ss = float(np.mean(v_plot[mask_ss]))

        # Respaldo por banda ±5% si el CSV venía recortado justo en el margen o sin caída (bloqueo)
        if t_estab_detectado is None:
            tol_5 = max(5.0, 0.05 * i_ss)
            fuera = np.where(np.abs(i_plot - i_ss) > tol_5)[0]
            if len(fuera) > 0 and (fuera[-1] + 1) < len(t_plot):
                t_estab_detectado = float(t_plot[fuera[-1] + 1])

        # Constante de tiempo τ (caída del 63.2% desde el pico hacia Iss)
        if i_max > i_ss * 1.02:
            i_tau_obj = i_max - 0.632 * (i_max - i_ss)
            t_post = t_plot[idx_pico:]
            i_post = i_plot[idx_pico:]
            cruces = np.where(i_post <= i_tau_obj)[0]
            if len(cruces) > 0:
                k_c = int(cruces[0])
                if k_c > 0 and i_post[k_c - 1] != i_post[k_c]:
                    frac = (i_post[k_c - 1] - i_tau_obj) / (i_post[k_c - 1] - i_post[k_c])
                    t_tau = float(t_post[k_c - 1] + frac * (t_post[k_c] - t_post[k_c - 1]))
                else:
                    t_tau = float(t_post[k_c])
                tau_ms = max(0.0, (t_tau - t_pico) * 1000.0)
            else:
                t_tau = t_pico
                tau_ms = 0.0
        else:
            # Curva plana sin caída transitoria apreciable (típico en rotor bloqueado)
            i_tau_obj = i_max
            t_tau = t_pico
            tau_ms = 0.0

        # Potencia instantánea P(t) [W] y Energía de arranque [J]
        p_inst = v_plot * (i_plot / 1000.0)
        p_max = float(np.max(p_inst))
        p_ss = float(np.mean(p_inst[mask_ss]))

        t_limite_e = t_estab_detectado if t_estab_detectado is not None else float(t_plot[-1])
        mask_e = t_plot <= t_limite_e
        e_arranque = integrar_trapecio(p_inst[mask_e], t_plot[mask_e])
        e_total = integrar_trapecio(p_inst, t_plot)

        # Métricas de frecuencia de adquisición
        dt_us_all = np.diff(t_raw_plot) * 1e6
        dt_med_us = float(np.median(dt_us_all)) if len(dt_us_all) > 0 else 0.0
        fs_real_hz = (
            float(1e6 / np.mean(dt_us_all))
            if (len(dt_us_all) > 0 and np.mean(dt_us_all) > 0)
            else 0.0
        )

        return TransientAnalysisResult(
            source_name=path.name,
            label=label,
            t_raw=t_raw_plot,
            v_raw=v_raw_plot,
            i_raw_ma=i_raw_plot,
            t_uni=t_plot,
            v_smooth=v_plot,
            i_smooth_ma=i_plot,
            p_inst_w=p_inst,
            i_pico_crudo_ma=i_max_crudo,
            i_pico_suave_ma=i_max,
            t_pico_ms=t_pico * 1000.0,
            i_ss_ma=i_ss,
            v_ss_v=v_ss,
            t_estab_ms=(t_estab_detectado * 1000.0) if t_estab_detectado is not None else None,
            t_corte_ms=float(t_plot[-1]) * 1000.0,
            tau_ms=tau_ms,
            t_tau_s=t_tau,
            i_tau_obj_ma=i_tau_obj,
            p_max_w=p_max,
            p_ss_w=p_ss,
            e_arranque_j=e_arranque,
            e_total_j=e_total,
            fs_real_hz=fs_real_hz,
            dt_med_us=dt_med_us,
            n_pre_samples=n_pre,
            n_active_samples=len(t_raw_plot),
            metadata={
                "v_offset_v": round(v_off, 4),
                "i_offset_ma": round(i_off, 3),
                "esquema_detectado": "SRAM_Tiempo_Relativo" if ya_disparado else "Timestamp_Absoluto",
            },
        )

    def analyze_folder(self, folder_path: str | Path) -> Tuple[List[TransientAnalysisResult], pd.DataFrame]:
        """Procesa todos los CSV de una carpeta y devuelve los resultados + DataFrame para la IA."""
        folder = Path(folder_path)
        results: List[TransientAnalysisResult] = []
        rows: List[Dict[str, Any]] = []

        for csv_file in sorted(folder.glob("*.csv")):
            try:
                res = self.analyze_csv(csv_file)
                results.append(res)
                rows.append(res.to_feature_dict())
            except Exception as exc:
                print(f"[ADVERTENCIA] Omitido '{csv_file.name}': {exc}")

        return results, pd.DataFrame(rows)


def graficar_transitorio_sram(res: TransientAnalysisResult, diagnostico_ia: Optional[str] = None) -> None:
    """
    Renderiza la gráfica dual (Voltaje y Corriente) anclada en t=0, con doble trazo
    crudo/suavizado, marcadores de pico, tau (63.2%), estabilización e Iss/Vss.
    """
    fig, (ax_v, ax_i) = plt.subplots(1, 2, figsize=(15, 6.5))
    titulo_ventana = f"Análisis Transitorio — {res.source_name} [{res.label}]"
    if fig.canvas.manager is not None:
        fig.canvas.manager.set_window_title(titulo_ventana)
    plt.subplots_adjust(left=0.06, right=0.98, top=0.88, bottom=0.12, wspace=0.22)

    # Panel Izquierdo: Voltaje vs Tiempo (anclado en 0,0)
    ax_v.plot(res.t_raw, res.v_raw, color="#ff9999", lw=0.7, alpha=0.45, label="Crudo SRAM")
    ax_v.plot(res.t_uni, res.v_smooth, "r-", lw=2.2, label="Voltaje Suavizado (V)")
    ax_v.axhline(y=res.v_ss_v, color="darkred", linestyle=":", lw=1.3, alpha=0.85, label=f"Vss = {res.v_ss_v:.2f} V")
    ax_v.set_title(f"Voltaje en Transitorio (Vss = {res.v_ss_v:.2f} V)", color="darkgreen", fontweight="bold")
    ax_v.set_xlabel("Tiempo desde arranque, t [s]")
    ax_v.set_ylabel("Voltaje, V(t) [V]")
    ax_v.set_xlim(0.0, res.t_uni[-1])
    ax_v.set_ylim(0.0, max(1.0, float(np.max(res.v_raw)) * 1.15))
    ax_v.grid(True, linestyle=":", alpha=0.75)
    ax_v.legend(loc="lower right")

    # Panel Derecho: Corriente vs Tiempo (anclado en 0,0)
    ax_i.plot(res.t_raw, res.i_raw_ma, color="#99bbff", lw=0.7, alpha=0.45, label="Crudo SRAM")
    ax_i.plot(res.t_uni, res.i_smooth_ma, "b-", lw=2.2, label="Corriente Suavizada (mA)")

    t_pico_s = res.t_pico_ms / 1000.0
    ax_i.plot(t_pico_s, res.i_pico_suave_ma, "ro", markersize=6, zorder=5, label=f"Pico: {res.i_pico_suave_ma:.1f} mA")
    ax_i.plot(res.t_tau_s, res.i_tau_obj_ma, "go", markersize=6, zorder=5, label=f"63.2% (τ = {res.tau_ms:.1f} ms)")
    ax_i.axvline(x=res.t_tau_s, color="g", linestyle="--", lw=1.2, alpha=0.8)

    if res.t_estab_ms is not None:
        t_est_s = res.t_estab_ms / 1000.0
        ax_i.axvline(
            x=t_est_s, color="#d97706", linestyle="-.", lw=1.2, alpha=0.85,
            label=f"Estable: {res.t_estab_ms:.0f} ms"
        )

    ax_i.axhline(y=res.i_ss_ma, color="purple", linestyle=":", lw=1.3, alpha=0.85, label=f"Iss = {res.i_ss_ma:.1f} mA")
    ax_i.set_title(
        f"Transitorio Detenido en {res.t_corte_ms:.0f} ms (τ = {res.tau_ms:.2f} ms)",
        color="darkgreen", fontweight="bold"
    )
    ax_i.set_xlabel("Tiempo desde arranque, t [s]")
    ax_i.set_ylabel("Corriente, I(t) [mA]")
    ax_i.set_xlim(0.0, res.t_uni[-1])
    ax_i.set_ylim(0.0, max(100.0, float(np.max(res.i_raw_ma)) * 1.10))
    ax_i.grid(True, linestyle=":", alpha=0.75)
    ax_i.legend(loc="upper right")

    estab_txt = f"{res.t_estab_ms:.0f} ms" if res.t_estab_ms is not None else "Fin búfer"
    diag_linea = f"\nDIAGNÓSTICO IA : {diagnostico_ia}" if diagnostico_ia else ""
    resumen = (
        f"TRANSITORIO SRAM ({res.n_active_samples} pts)\n"
        f"──────────────────────────────\n"
        f"Estabilización : {estab_txt} (corte {res.t_corte_ms:.0f} ms)\n"
        f"I pico crudo   : {res.i_pico_crudo_ma:.1f} mA ({res.i_pico_crudo_ma/1000:.2f} A)\n"
        f"I pico suave   : {res.i_pico_suave_ma:.1f} mA ({res.i_pico_suave_ma/1000:.2f} A)\n"
        f"I estable (Iss): {res.i_ss_ma:.1f} mA ({res.i_ss_ma/1000:.2f} A)\n"
        f"V estable (Vss): {res.v_ss_v:.2f} V\n"
        f"Constante τ    : {res.tau_ms:.2f} ms\n"
        f"Potencia máx.  : {res.p_max_w:.2f} W (Pss = {res.p_ss_w:.2f} W)\n"
        f"Energía arranq.: {res.e_arranque_j:.3f} J\n"
        f"Muestreo CSV   : {res.fs_real_hz:.0f} Hz (Δt={res.dt_med_us:.0f} µs)"
        f"{diag_linea}"
    )
    ax_i.text(
        0.97, 0.52, resumen, transform=ax_i.transAxes,
        fontsize=9.2, verticalalignment="top", horizontalalignment="right",
        bbox=dict(boxstyle="round,pad=0.5", facecolor="#f8f9fa", edgecolor="#2b5c8f", alpha=0.92),
    )

    plt.show()


if __name__ == "__main__":
    configurar_salida_utf8()

    ruta_csv = resolver_csv_por_defecto(sys.argv)

    procesador = TransientProcessor()
    resultado = procesador.analyze_csv(ruta_csv)

    print("\n" + "=" * 58)
    print(f" REPORTE DE PARÁMETROS DEL TRANSITORIO — {resultado.source_name}")
    print("=" * 58)
    for clave, valor in resultado.to_feature_dict().items():
        print(f" {clave:<20}: {valor}")
    print("=" * 58)

    graficar_transitorio_sram(resultado)