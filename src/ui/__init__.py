"""
Paquete: src/ui
Interfaz gráfica industrial CAD/CAE para diagnóstico de motores DC (Expo ICE Impulsa 2026).
"""

from src.ui.cad_theme import apply_cad_theme, SEVERITY_STYLES, STATE_LABELS_ES
from src.ui.motor_spec_panel import MotorSpecPanel
from src.ui.transient_canvas import TransientCanvas
from src.ui.main_window import MainWindow

__all__ = [
    "apply_cad_theme",
    "SEVERITY_STYLES",
    "STATE_LABELS_ES",
    "MotorSpecPanel",
    "TransientCanvas",
    "MainWindow",
]