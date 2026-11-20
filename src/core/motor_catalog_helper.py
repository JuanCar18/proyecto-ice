import os
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    from src.core.motor_profile import MotorSpecProfile
except ImportError:
    MotorSpecProfile = None


def _leer_primer_atributo(obj: Any, nombres: Tuple[str, ...], default: float = 0.0) -> float:
    for nombre in nombres:
        if hasattr(obj, nombre):
            val = getattr(obj, nombre)
            if val is not None:
                try:
                    fval = float(val)
                    if np.isfinite(fval) and abs(fval) > 1e-9:
                        return fval
                except (TypeError, ValueError):
                    pass
    return default


def extraer_metricas_res(res: Any) -> Dict[str, float]:
    """Extrae Vss, Iss, Ipico y Tau de res con respaldo directo sobre las series/CSV."""
    v_ss = _leer_primer_atributo(res, ("v_ss", "v_estable", "voltaje_estable", "voltaje_ss", "v_steady", "v_mean"))
    i_ss = _leer_primer_atributo(res, ("i_ss", "i_estable", "corriente_estable", "corriente_ss", "i_steady", "i_ss_ma"))
    i_pico = _leer_primer_atributo(
        res, ("i_pico_suave", "i_pico_crudo", "i_pico", "corriente_pico", "i_max", "i_peak", "i_peak_ma")
    )
    tau_ms = _leer_primer_atributo(res, ("tau_ms", "constante_tau_ms", "tau"))
    if 0.0 < tau_ms < 2.0 and hasattr(res, "tau") and not hasattr(res, "tau_ms"):
        tau_ms *= 1000.0

    if v_ss == 0.0 or i_ss == 0.0 or i_pico == 0.0:
        t_arr, v_arr, i_arr = None, None, None

        for t_attr, v_attr, i_attr in (("t", "v", "i"), ("tiempo", "voltaje", "corriente"), ("t_s", "v_v", "i_ma")):
            if hasattr(res, t_attr) and hasattr(res, v_attr) and hasattr(res, i_attr):
                t_arr = np.asarray(getattr(res, t_attr), dtype=float)
                v_arr = np.asarray(getattr(res, v_attr), dtype=float)
                i_arr = np.asarray(getattr(res, i_attr), dtype=float)
                break

        if (t_arr is None or len(t_arr) < 5) and hasattr(res, "df") and getattr(res, "df") is not None:
            df = getattr(res, "df")
            if {"Tiempo(s)", "Voltaje(V)", "Corriente(mA)"}.issubset(df.columns):
                t_arr = df["Tiempo(s)"].to_numpy(dtype=float)
                v_arr = df["Voltaje(V)"].to_numpy(dtype=float)
                i_arr = df["Corriente(mA)"].to_numpy(dtype=float)

        if t_arr is None or len(t_arr) < 5:
            for p_attr in ("file_path", "csv_path", "path", "source_path"):
                ruta = getattr(res, p_attr, None)
                if ruta and os.path.exists(str(ruta)):
                    try:
                        data = np.genfromtxt(str(ruta), delimiter=",", skip_header=1, usecols=(0, 1, 2))
                        if data.ndim == 2 and len(data) >= 5:
                            t_arr, v_arr, i_arr = data[:, 0], data[:, 1], data[:, 2]
                    except Exception:
                        pass
                    break

        if t_arr is not None and len(t_arr) >= 5:
            activos = np.where((v_arr > 1.0) | (i_arr > 5.0))[0]
            if len(activos) >= 5:
                v_act = v_arr[activos]
                i_act = i_arr[activos]
                t_act = t_arr[activos]

                if i_pico == 0.0:
                    i_pico = float(np.max(i_act))
                ini_ss = max(1, int(len(i_act) * 0.70))
                if v_ss == 0.0:
                    v_ss = float(np.mean(v_act[ini_ss:]))
                if i_ss == 0.0:
                    i_ss = float(np.mean(i_act[ini_ss:]))
                if tau_ms == 0.0 and i_pico > i_ss:
                    idx_pk = int(np.argmax(i_act))
                    objetivo = i_pico - 0.632 * (i_pico - i_ss)
                    post = np.where(i_act[idx_pk:] <= objetivo)[0]
                    if len(post) > 0:
                        tau_ms = max(0.0, float((t_act[idx_pk + post[0]] - t_act[idx_pk]) * 1000.0))

    return {
        "v_ss": v_ss,
        "i_ss": i_ss,
        "i_pico": i_pico,
        "tau_ms": tau_ms,
    }


class MotorCatalogAssistant:
    """Gestiona la carga de motores desde SQLite y asiste el diagnóstico físico con datos de placa."""

    def __init__(self, db_path: str):
        self.db_path = os.path.abspath(db_path)
        self.motores: List[Dict[str, Any]] = []
        self.motor_activo: Optional[Dict[str, Any]] = None

    def cargar_motores_bd(self) -> List[Dict[str, Any]]:
        self.motores = []
        if not os.path.exists(self.db_path):
            return self.motores

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tablas = [r["name"] for r in cur.fetchall()]

            tabla_obj = next(
                (t for t in ("motores", "catalogo_motores", "motor_specs") if t in tablas),
                None,
            )
            if not tabla_obj:
                return self.motores

            cur.execute(f"SELECT * FROM {tabla_obj} ORDER BY 1 ASC")
            for row in cur.fetchall():
                d = dict(row)
                modelo = (
                    d.get("modelo")
                    or d.get("nombre")
                    or d.get("motor_nombre")
                    or f"Motor #{d.get('id', '?')}"
                )
                self.motores.append({
                    "id": d.get("id") or d.get("motor_id"),
                    "modelo": str(modelo),
                    "v_min": float(d.get("v_min") or d.get("voltaje_min") or 0.0),
                    "v_max": float(d.get("v_max") or d.get("voltaje_max") or 0.0),
                    "v_nom": float(d.get("v_nom") or d.get("voltaje_nom") or d.get("voltaje_nominal") or 12.0),
                    "r_ohm": float(d.get("rw_ohm") or d.get("r_armadura_ohm") or d.get("resistencia_ohm") or 0.0),
                    "l_mh": float(d.get("l_mh") or d.get("l_armadura_mh") or d.get("inductancia_mh") or 0.0),
                    "i_nom_a": float(d.get("i_nom_a") or d.get("corriente_nom_a") or d.get("corriente_nominal_a") or 0.0),
                    "i_stall_a": float(d.get("i_stall_a") or d.get("corriente_stall_a") or d.get("corriente_arranque_a") or 0.0),
                    "notas": str(d.get("descripcion") or d.get("notas") or ""),
                })
        finally:
            conn.close()

        return self.motores

    def construir_perfil_motor(self, motor_dict: Optional[Dict[str, Any]]):
        self.motor_activo = motor_dict
        if not motor_dict or MotorSpecProfile is None:
            return None

        try:
            return MotorSpecProfile(
                modelo=motor_dict["modelo"],
                v_min=motor_dict["v_min"],
                v_max=motor_dict["v_max"],
                v_nom=motor_dict["v_nom"],
                rw_ohm=motor_dict["r_ohm"],
                l_mh=motor_dict["l_mh"],
                i_nom_a=motor_dict["i_nom_a"],
                i_stall_a=motor_dict["i_stall_a"],
            )
        except TypeError:
            perfil = MotorSpecProfile()
            for attr, val in (
                ("modelo", motor_dict["modelo"]),
                ("nombre", motor_dict["modelo"]),
                ("v_min", motor_dict["v_min"]),
                ("v_max", motor_dict["v_max"]),
                ("v_nom", motor_dict["v_nom"]),
                ("rw_ohm", motor_dict["r_ohm"]),
                ("r_armadura_ohm", motor_dict["r_ohm"]),
                ("l_mh", motor_dict["l_mh"]),
                ("l_armadura_mh", motor_dict["l_mh"]),
                ("i_nom_a", motor_dict["i_nom_a"]),
                ("i_stall_a", motor_dict["i_stall_a"]),
            ):
                if hasattr(perfil, attr):
                    setattr(perfil, attr, val)
            return perfil

    def evaluar_asistencia_fisica(self, res, report) -> Dict[str, Any]:
        metricas = extraer_metricas_res(res)
        v_ss = metricas["v_ss"]
        i_ss_ma = metricas["i_ss"]
        i_pico_ma = metricas["i_pico"]
        tau_ms = metricas["tau_ms"]
        r_est = (v_ss / (i_pico_ma / 1000.0)) if i_pico_ma > 10.0 else 0.0

        estado_ia = str(getattr(report, "estado_detectado", "DESCONOCIDO")).upper()
        confianza = float(getattr(report, "confianza_pct", 0.0))
        severidad = str(getattr(report, "severidad", "INFO"))

        if not self.motor_activo:
            comparativa_generica = [
                ("Voltaje Estable (Vss)", f"{v_ss:.2f} V", "Selecciona motor en BD", "MEDIDO"),
                ("Corriente Estable (Iss)", f"{i_ss_ma:.1f} mA", "Selecciona motor en BD", "MEDIDO"),
                ("Corriente Pico Arranque", f"{i_pico_ma:.1f} mA", "Selecciona motor en BD", "MEDIDO"),
                ("Constante de Tiempo (τ)", f"{tau_ms:.2f} ms", "Dinámica electromecánica", "MEDIDO"),
                ("Resistencia Armadura Est.", f"{r_est:.2f} Ω", "Estimada Vss / Ipico", "ESTIMADO"),
            ]
            return {
                "motor_nombre": "Sin especificar (Modo genérico IA)",
                "estado_final": estado_ia,
                "confianza_pct": confianza,
                "severidad": severidad,
                "ajuste_aplicado": False,
                "metricas": metricas,
                "observaciones": [
                    "Elige un motor en el desplegable 'Motor BD' superior para contrastar Vss, Iss, Ipico y Rw contra placa."
                ],
                "comparativa": comparativa_generica,
            }

        m = self.motor_activo
        v_nom = m["v_nom"]
        v_min = m["v_min"] if m["v_min"] > 0 else (0.85 * v_nom)
        v_max = m["v_max"] if m["v_max"] > 0 else (1.15 * v_nom)
        i_nom_ma = m["i_nom_a"] * 1000.0
        i_stall_ma = m["i_stall_a"] * 1000.0

        observaciones = []
        comparativa = []
        estado_final = estado_ia
        ajuste_aplicado = False

        if v_nom > 0:
            en_rango_v = (v_min * 0.95) <= v_ss <= (v_max * 1.05)
            comparativa.append((
                "Voltaje Estable (Vss)",
                f"{v_ss:.2f} V",
                f"{v_min:.1f}–{v_max:.1f} V (Nom: {v_nom:.1f} V)",
                "OK" if en_rango_v else "FUERA DE RANGO",
            ))

            if v_ss > 0.5 and v_ss < (v_min * 0.92):
                observaciones.append(
                    f"Subtensión confirmada por placa: Vss={v_ss:.2f} V < mínimo nominal ({v_min:.2f} V)."
                )
                if estado_final == "NORMAL":
                    estado_final = "BAJO_VOLTAJE"
                    severidad = "ADVERTENCIA"
                    ajuste_aplicado = True
            elif en_rango_v and estado_final == "BAJO_VOLTAJE":
                observaciones.append(
                    f"Corrección por catálogo: Vss={v_ss:.2f} V es normal para '{m['modelo']}' ({v_min:.1f}–{v_max:.1f} V)."
                )
                estado_final = "NORMAL"
                severidad = "NORMAL"
                ajuste_aplicado = True
        else:
            comparativa.append(("Voltaje Estable (Vss)", f"{v_ss:.2f} V", "No definido en BD", "MEDIDO"))

        if i_nom_ma > 0:
            ratio_inom = i_ss_ma / i_nom_ma
            comparativa.append((
                "Corriente Estable (Iss)",
                f"{i_ss_ma:.1f} mA",
                f"≤ {i_nom_ma:.0f} mA ({m['i_nom_a']:.2f} A)",
                "OK" if ratio_inom <= 1.20 else "EXCESO DE CARGA",
            ))

            if i_stall_ma > 0 and i_ss_ma >= (0.65 * i_stall_ma):
                observaciones.append(
                    f"Corriente estable crítica ({i_ss_ma:.0f} mA ≥ 65% de I_stall={i_stall_ma:.0f} mA): rotor severamente frenado."
                )
                if estado_final in ("NORMAL", "ROZAMIENTO_MECANICO"):
                    estado_final = "BLOQUEO_PARCIAL"
                    severidad = "CRÍTICO"
                    ajuste_aplicado = True
            elif ratio_inom > 1.25:
                if tau_ms > 280.0:
                    observaciones.append(
                        f"Iss ({i_ss_ma:.0f} mA) supera en {(ratio_inom - 1)*100:.0f}% a I_nom con arranque lento (τ={tau_ms:.0f} ms) → Sobrecarga."
                    )
                    if estado_final == "NORMAL":
                        estado_final = "SOBRECARGA"
                        severidad = "ADVERTENCIA"
                        ajuste_aplicado = True
                else:
                    observaciones.append(
                        f"Iss ({i_ss_ma:.0f} mA) supera en {(ratio_inom - 1)*100:.0f}% a I_nom con τ corto ({tau_ms:.0f} ms) → Rozamiento."
                    )
                    if estado_final == "NORMAL":
                        estado_final = "ROZAMIENTO_MECANICO"
                        severidad = "ADVERTENCIA"
                        ajuste_aplicado = True
        else:
            comparativa.append(("Corriente Estable (Iss)", f"{i_ss_ma:.1f} mA", "No definido en BD", "MEDIDO"))

        if i_stall_ma > 0:
            comparativa.append((
                "Corriente Pico Arranque",
                f"{i_pico_ma:.1f} mA",
                f"Stall ref: {i_stall_ma:.0f} mA ({m['i_stall_a']:.2f} A)",
                "OK" if i_pico_ma <= i_stall_ma * 1.15 else "PICO ELEVADO",
            ))
        else:
            comparativa.append(("Corriente Pico Arranque", f"{i_pico_ma:.1f} mA", "No definido en BD", "MEDIDO"))

        comparativa.append(("Constante de Tiempo (τ)", f"{tau_ms:.2f} ms", "Respuesta transitoria", "MEDIDO"))

        ref_rw = f"Catálogo Rw: {m['r_ohm']:.2f} Ω" if m["r_ohm"] > 0 else "No definido en BD"
        comparativa.append(("Resistencia Armadura Est.", f"{r_est:.2f} Ω", ref_rw, "REFERENCIA"))

        if not observaciones:
            observaciones.append(
                f"Parámetros eléctricos y transitorios coherentes con la ficha técnica de '{m['modelo']}'."
            )

        return {
            "motor_nombre": m["modelo"],
            "estado_final": estado_final,
            "confianza_pct": confianza,
            "severidad": severidad,
            "ajuste_aplicado": ajuste_aplicado,
            "metricas": metricas,
            "observaciones": observaciones,
            "comparativa": comparativa,
        }