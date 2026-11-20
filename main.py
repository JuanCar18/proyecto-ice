"""
### 2. `main.py` — Instanciación suelta de `DatabaseManager` (Importante)
*Hallazgo:** Las líneas `db = DatabaseManager()` y `motores = db.obtener_todos_los_motores()` están a nivel global (fuera de `def main()`) y sus variables nunca se envían a `MainWindow`. Si `main.py` se importa desde otro script o ocurre una excepción en la BD, fallará antes de iniciar `QApplication`, además de dejar `motores` sin usar en memoria.
* **Corrección:** Mueve `db = DatabaseManager()` dentro de `main()` junto con `processor` y `ai_engine`, y pásalo a `MainWindow` (en el siguiente paso ajustaremos `main_window.py` para que acepte `db_manager=db` de forma retrocompatible):

python
"""
"""
Archivo: main.py
Proyecto: Diagnóstico Inteligente de Motores DC - Expo ICE Impulsa 2026
Descripción:
    Punto de entrada principal de la estación de post-procesamiento,
    graficación de transitorios SRAM y diagnóstico automático por IA ligera.

Uso:
    python main.py
    python main.py datasets/raw/
    python main.py datasets/raw/dataset_Motor_prueba_12V.csv
"""

import os
import sys
import traceback
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import QApplication, QComboBox, QGroupBox, QAction, QShortcut

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.ui.main_window import MainWindow
from src.ui.esp32_dock_panel import ESP32ControlDock
from src.ui.diagnosis_window import VentanaDiagnosticoIA
from src.core.motor_catalog_helper import MotorCatalogAssistant

try:
    from src.ui.cad_theme import aplicar_tema_cad
except ImportError:
    aplicar_tema_cad = None


def _integrar_catalogo_y_ventana_diagnostico(ventana: MainWindow, dock_esp32: ESP32ControlDock):
    db_path = os.path.join(BASE_DIR, "database", "ice2026_motores.db")
    asistente_bd = MotorCatalogAssistant(db_path)
    motores_bd = asistente_bd.cargar_motores_bd()
    ventana._catalog_assistant = asistente_bd

    win_diag = VentanaDiagnosticoIA(ventana)
    win_diag.poblar_motores_bd(motores_bd, indice_activo=0)
    ventana._ventana_diagnostico = win_diag

    def mostrar_ventana_diagnostico(enfocar: bool = True):
        if not win_diag.isVisible():
            win_diag.show()
        if enfocar:
            win_diag.raise_()
            win_diag.activateWindow()

    combo_motores = None
    for cmb in ventana.findChildren(QComboBox):
        if not win_diag.isAncestorOf(cmb):
            combo_motores = cmb
            break

    # Ocultar SOLO la tarjeta duplicada dentro de MainWindow (sin tocar la de win_diag)
    for grp in ventana.findChildren(QGroupBox):
        if not win_diag.isAncestorOf(grp) and "probabilidad por escenario" in grp.title().lower():
            grp.setVisible(False)

    def _actualizar_diagnostico_activo(abrir_ventana: bool = True):
        records = getattr(ventana, "_records", [])
        idx = getattr(ventana, "_current_index", -1)
        if not records or idx < 0 or idx >= len(records):
            return

        res, report = records[idx]
        info_bd = asistente_bd.evaluar_asistencia_fisica(res, report)

        if hasattr(report, "estado_detectado"):
            report.estado_detectado = info_bd["estado_final"]
        if hasattr(ventana, "_refresh_dataset_table"):
            ventana._refresh_dataset_table()

        win_diag.actualizar_diagnostico(res, report, info_bd)
        if abrir_ventana:
            mostrar_ventana_diagnostico(enfocar=True)

    def _aplicar_indice_motor_bd(indice: int, origen_popup: bool = False):
        if combo_motores is not None and combo_motores.currentIndex() != indice:
            combo_motores.blockSignals(True)
            combo_motores.setCurrentIndex(indice)
            combo_motores.blockSignals(False)

        if win_diag.cmb_motor_bd.currentIndex() != indice:
            win_diag.cmb_motor_bd.blockSignals(True)
            win_diag.cmb_motor_bd.setCurrentIndex(indice)
            win_diag.cmb_motor_bd.blockSignals(False)

        if indice <= 0 or (indice - 1) >= len(motores_bd):
            asistente_bd.motor_activo = None
            if hasattr(ventana, "ai_engine") and hasattr(ventana.ai_engine, "set_motor_profile"):
                ventana.ai_engine.set_motor_profile(None)
            if hasattr(ventana, "_log_console"):
                ventana._log_console("[CATÁLOGO BD] Modo sin motor específico seleccionado.")
            _actualizar_diagnostico_activo(abrir_ventana=origen_popup)
            return

        motor_sel = motores_bd[indice - 1]
        perfil = asistente_bd.construir_perfil_motor(motor_sel)
        if hasattr(ventana, "ai_engine") and hasattr(ventana.ai_engine, "set_motor_profile"):
            ventana.ai_engine.set_motor_profile(perfil)

        if hasattr(ventana, "_log_console"):
            ventana._log_console(
                f"[CATÁLOGO BD] Motor aplicado: '{motor_sel['modelo']}' "
                f"(Vnom={motor_sel['v_nom']} V, Inom={motor_sel['i_nom_a']} A, Istall={motor_sel['i_stall_a']} A)"
            )
        _actualizar_diagnostico_activo(abrir_ventana=True)

    if combo_motores is not None:
        if combo_motores.count() <= 1 and motores_bd:
            combo_motores.clear()
            combo_motores.addItem("-- Entrada manual / Sin especificar --")
            for m in motores_bd:
                combo_motores.addItem(f"{m['modelo']} ({m['v_nom']}V / {m['i_nom_a']}A)")
        combo_motores.currentIndexChanged.connect(lambda idx: _aplicar_indice_motor_bd(idx, origen_popup=False))

    win_diag.motor_cambiado_en_ventana.connect(lambda idx: _aplicar_indice_motor_bd(idx, origen_popup=True))

    if hasattr(ventana, "_display_record") and callable(ventana._display_record):
        display_original = ventana._display_record

        def _display_record_con_ventana(row: int):
            display_original(row)
            _actualizar_diagnostico_activo(abrir_ventana=True)

        ventana._display_record = _display_record_con_ventana

    def _ir_anterior():
        idx = getattr(ventana, "_current_index", 0)
        if idx > 0 and hasattr(ventana, "table_datasets"):
            ventana.table_datasets.selectRow(idx - 1)
            ventana._display_record(idx - 1)

    def _ir_siguiente():
        records = getattr(ventana, "_records", [])
        idx = getattr(ventana, "_current_index", 0)
        if idx + 1 < len(records) and hasattr(ventana, "table_datasets"):
            ventana.table_datasets.selectRow(idx + 1)
            ventana._display_record(idx + 1)

    win_diag.solicitar_anterior.connect(_ir_anterior)
    win_diag.solicitar_siguiente.connect(_ir_siguiente)

    accion_abrir_diag = QAction("Abrir Ventana de Diagnóstico (Ctrl+D)", ventana)
    accion_abrir_diag.triggered.connect(lambda: mostrar_ventana_diagnostico(enfocar=True))
    ventana.addAction(accion_abrir_diag)

    atajo_diag = QShortcut(QKeySequence("Ctrl+D"), ventana)
    atajo_diag.activated.connect(lambda: mostrar_ventana_diagnostico(enfocar=True))

    for accion_menu in ventana.menuBar().actions():
        if "ver" in accion_menu.text().lower() and accion_menu.menu() is not None:
            accion_menu.menu().addAction(accion_abrir_diag)
            break

    if hasattr(ventana, "_log_console"):
        ventana._log_console(
            f"[SISTEMA] Catálogo SQLite verificado ({len(motores_bd)} motores) y ventana de diagnóstico lista (Ctrl+D)."
        )


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Estación de Diagnóstico Inteligente de Motores DC — Expo ICE Impulsa 2026")

    if callable(aplicar_tema_cad):
        aplicar_tema_cad(app)

    ventana = MainWindow()

    dock_esp32 = ESP32ControlDock(
        ventana,
        carpeta_capturas=os.path.join(BASE_DIR, "capturas_esp32")
    )
    ventana.addDockWidget(Qt.RightDockWidgetArea, dock_esp32)

    _integrar_catalogo_y_ventana_diagnostico(ventana, dock_esp32)

    def manejar_excepcion_global(exc_type, exc_value, exc_tb):
        detalle = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        if hasattr(ventana, "_log_console"):
            ventana._log_console(f"[ANOMALÍA SISTEMA]\n{detalle}")
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = manejar_excepcion_global

    for accion_menu in ventana.menuBar().actions():
        if "ver" in accion_menu.text().lower() and accion_menu.menu() is not None:
            accion_menu.menu().addSeparator()
            accion_menu.menu().addAction(dock_esp32.toggleViewAction())
            break

    app.aboutToQuit.connect(dock_esp32.worker.desactivar_entrada)

    ventana.showMaximized()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()