"""
Módulo: src/core/motor_profile.py
Proyecto: Diagnóstico Inteligente de Motores DC - Expo ICE Impulsa 2026
Descripción:
    Estructura de datos opcional para especificaciones nominales de placa
    o catálogo SQLite/MySQL del motor DC bajo prueba.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class MotorSpecProfile:
    """Parámetros nominales opcionales del motor DC (todos los numéricos admiten None)."""

    id_motor: Optional[int] = None
    modelo: str = "No especificado"
    fabricante: str = ""
    v_nom_min: Optional[float] = None
    v_nom_max: Optional[float] = None
    v_nom: Optional[float] = None
    rw_ohm: Optional[float] = None
    l_mh: Optional[float] = None
    i_vacio_a: Optional[float] = None
    i_nominal_a: Optional[float] = None
    i_stall_a: Optional[float] = None
    rpm_nominal: Optional[int] = None
    fuente_datos: str = "NO_ESPECIFICADO"

    def tiene_datos(self) -> bool:
        """Indica si existe al menos un parámetro eléctrico o rango de voltaje definido."""
        return any(
            val is not None and val > 0
            for val in (
                self.v_nom_min,
                self.v_nom_max,
                self.v_nom,
                self.rw_ohm,
                self.l_mh,
                self.i_vacio_a,
                self.i_nominal_a,
                self.i_stall_a,
            )
        )

    def voltaje_referencia(self) -> Optional[float]:
        """Devuelve el voltaje nominal central o el punto medio positivo del rango."""
        if self.v_nom is not None and self.v_nom > 0:
            return float(self.v_nom)
        if (
            self.v_nom_min is not None
            and self.v_nom_max is not None
            and (self.v_nom_min > 0 or self.v_nom_max > 0)
        ):
            return 0.5 * (float(self.v_nom_min) + float(self.v_nom_max))
        if self.v_nom_min is not None and self.v_nom_min > 0:
            return float(self.v_nom_min)
        if self.v_nom_max is not None and self.v_nom_max > 0:
            return float(self.v_nom_max)
        return None

    def resumen_corto(self) -> str:
        """Texto compacto para mostrar en cabecera o dictamen."""
        if not self.tiene_datos():
            return "Motor sin parámetros de placa (inferencia pura por curva)"
        partes = []
        nombre = f"{self.fabricante} {self.modelo}".strip()
        if nombre and nombre != "No especificado":
            partes.append(nombre)
        if (
            self.v_nom_min is not None
            and self.v_nom_max is not None
            and self.v_nom_max > 0
        ):
            partes.append(f"Rango: {self.v_nom_min:g}–{self.v_nom_max:g} V")
        elif self.v_nom is not None and self.v_nom > 0:
            partes.append(f"V_nom: {self.v_nom:g} V")
        if self.rw_ohm is not None and self.rw_ohm > 0:
            partes.append(f"Rw: {self.rw_ohm:g} Ω")
        if self.i_nominal_a is not None and self.i_nominal_a > 0:
            partes.append(f"I_nom: {self.i_nominal_a:g} A")
        if self.i_stall_a is not None and self.i_stall_a > 0:
            partes.append(f"I_stall: {self.i_stall_a:g} A")
        return " | ".join(partes)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id_motor": self.id_motor,
            "fabricante": self.fabricante,
            "modelo": self.modelo,
            "v_nom_min": self.v_nom_min,
            "v_nom_max": self.v_nom_max,
            "v_nom": self.v_nom,
            "rw_ohm": self.rw_ohm,
            "l_mh": self.l_mh,
            "i_vacio_a": self.i_vacio_a,
            "i_nominal_a": self.i_nominal_a,
            "i_stall_a": self.i_stall_a,
            "rpm_nominal": self.rpm_nominal,
            "fuente_datos": self.fuente_datos,
        }