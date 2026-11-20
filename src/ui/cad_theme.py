"""
Módulo: src/ui/cad_theme.py
Proyecto: Diagnóstico Inteligente de Motores DC - Expo ICE Impulsa 2026
Descripción:
    Hoja de estilos QSS y paleta de colores de estética industrial CAD/CAE
    (inspirada en Proteus, SolidWorks y LTspice) para la estación de análisis.
"""

from typing import Dict
from PyQt5.QtGui import QColor, QFont, QPalette
from PyQt5.QtWidgets import QApplication


SEVERITY_STYLES: Dict[str, Dict[str, str]] = {
    "OK": {
        "bg": "#dcfce7",
        "border": "#16a34a",
        "text": "#14532d",
        "badge": "#15803d",
    },
    "ADVERTENCIA": {
        "bg": "#fef3c7",
        "border": "#d97706",
        "text": "#78350f",
        "badge": "#b45309",
    },
    "CRITICO": {
        "bg": "#fee2e2",
        "border": "#dc2626",
        "text": "#7f1d1d",
        "badge": "#b91c1c",
    },
    "NEUTRO": {
        "bg": "#e2e8f0",
        "border": "#64748b",
        "text": "#1e293b",
        "badge": "#475569",
    },
}

STATE_LABELS_ES: Dict[str, str] = {
    "NORMAL": "ESC 1 — Operación Normal",
    "SOBRECARGA": "ESC 2 — Sobrecarga Mecánica",
    "BAJO_VOLTAJE": "ESC 3 — Bajo Voltaje",
    "ROZAMIENTO_MECANICO": "ESC 4 — Rozamiento Mecánico",
    "BLOQUEO_PARCIAL": "ESC 5 — Bloqueo Parcial / Rotor Estancado",
}

CAD_QSS = """
QMainWindow {
    background-color: #dde2e8;
}

QMenuBar {
    background-color: #1e293b;
    color: #f8fafc;
    border-bottom: 1px solid #0f172a;
    padding: 2px 6px;
}
QMenuBar::item {
    background: transparent;
    padding: 4px 10px;
}
QMenuBar::item:selected {
    background-color: #334155;
    border-radius: 3px;
}
QMenu {
    background-color: #f8fafc;
    color: #0f172a;
    border: 1px solid #94a3b8;
}
QMenu::item:selected {
    background-color: #2563eb;
    color: #ffffff;
}

QToolBar {
    background-color: #eef1f5;
    border-bottom: 1px solid #b8c0cc;
    spacing: 6px;
    padding: 4px 8px;
}
QToolButton {
    background-color: #ffffff;
    color: #1e293b;
    border: 1px solid #b8c0cc;
    border-radius: 3px;
    padding: 5px 10px;
    font-weight: 600;
}
QToolButton:hover {
    background-color: #e0ecff;
    border-color: #2563eb;
}
QToolButton:pressed {
    background-color: #cbd5e1;
}

QDockWidget {
    font-weight: bold;
    color: #1e293b;
}
QDockWidget::title {
    background-color: #cbd5e1;
    border: 1px solid #94a3b8;
    padding: 5px 8px;
    text-align: left;
}

QGroupBox {
    background-color: #f8fafc;
    border: 1px solid #b8c0cc;
    border-radius: 4px;
    margin-top: 10px;
    padding-top: 10px;
    font-weight: bold;
    color: #1e293b;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 8px;
    padding: 0 4px;
    background-color: #f8fafc;
    color: #0f172a;
}

QLineEdit, QComboBox {
    background-color: #ffffff;
    color: #0f172a;
    border: 1px solid #94a3b8;
    border-radius: 3px;
    padding: 3px 6px;
}
QLineEdit:focus, QComboBox:focus {
    border-color: #2563eb;
}

QTableWidget {
    background-color: #ffffff;
    alternate-background-color: #f1f5f9;
    gridline-color: #cbd5e1;
    border: 1px solid #94a3b8;
    selection-background-color: #dbeafe;
    selection-color: #0f172a;
    font-size: 9pt;
}
QHeaderView::section {
    background-color: #334155;
    color: #f8fafc;
    padding: 4px 6px;
    border: 1px solid #1e293b;
    font-weight: bold;
    font-size: 8.5pt;
}

QPushButton {
    background-color: #ffffff;
    color: #1e293b;
    border: 1px solid #94a3b8;
    border-radius: 3px;
    padding: 5px 10px;
    font-weight: 600;
}
QPushButton:hover {
    background-color: #e2e8f0;
    border-color: #475569;
}
QPushButton#PrimaryBtn {
    background-color: #1d4ed8;
    color: #ffffff;
    border: 1px solid #1e40af;
}
QPushButton#PrimaryBtn:hover {
    background-color: #2563eb;
}

QProgressBar {
    border: 1px solid #94a3b8;
    border-radius: 3px;
    background-color: #e2e8f0;
    text-align: center;
    height: 16px;
    font-family: "Consolas", "Cascadia Mono", monospace;
    font-size: 8.5pt;
    color: #0f172a;
}
QProgressBar::chunk {
    background-color: #2563eb;
    border-radius: 2px;
}

QTextEdit, QPlainTextEdit {
    background-color: #0f172a;
    color: #e2e8f0;
    border: 1px solid #334155;
    font-family: "Consolas", "Cascadia Mono", monospace;
    font-size: 9pt;
}

QStatusBar {
    background-color: #1e293b;
    color: #e2e8f0;
    border-top: 1px solid #0f172a;
    font-family: "Consolas", "Cascadia Mono", monospace;
    font-size: 8.5pt;
}
"""


def apply_cad_theme(app: QApplication) -> None:
    """Aplica la paleta técnica y la hoja de estilos QSS global a la aplicación."""
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 9))

    palette = QPalette()
    palette.setColor(QPalette.Window, QColor("#dde2e8"))
    palette.setColor(QPalette.WindowText, QColor("#0f172a"))
    palette.setColor(QPalette.Base, QColor("#ffffff"))
    palette.setColor(QPalette.AlternateBase, QColor("#f1f5f9"))
    palette.setColor(QPalette.ToolTipBase, QColor("#1e293b"))
    palette.setColor(QPalette.ToolTipText, QColor("#f8fafc"))
    palette.setColor(QPalette.Text, QColor("#0f172a"))
    palette.setColor(QPalette.Button, QColor("#eef1f5"))
    palette.setColor(QPalette.ButtonText, QColor("#0f172a"))
    palette.setColor(QPalette.Highlight, QColor("#2563eb"))
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)

    app.setStyleSheet(CAD_QSS)