"""
Paquete src.core — Procesamiento de transitorios, perfilado nominal,
motor de diagnóstico IA y persistencia SQLite.
"""

from src.core.ai_engine import (
    CANONICAL_STATES,
    FEATURE_COLUMNS,
    DiagnosticReport,
    MotorDiagnosticAI,
    normalizar_etiqueta_estado,
)
from src.core.db_manager import DatabaseManager
from src.core.motor_profile import MotorSpecProfile
from src.core.transient_processor import (
    TransientAnalysisResult,
    TransientProcessor,
    graficar_transitorio_sram,
)

__all__ = [
    "CANONICAL_STATES",
    "FEATURE_COLUMNS",
    "DatabaseManager",
    "DiagnosticReport",
    "MotorDiagnosticAI",
    "MotorSpecProfile",
    "TransientAnalysisResult",
    "TransientProcessor",
    "graficar_transitorio_sram",
    "normalizar_etiqueta_estado",
]