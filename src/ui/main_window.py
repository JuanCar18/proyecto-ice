"""
Módulo: src/ui/main_window.py
Proyecto: Diagnóstico Inteligente de Motores DC - Expo ICE Impulsa 2026
Descripción:
    Ventana principal de la estación de diagnóstico con distribución de 2 columnas
    + consola inferior de ancho completo:
      - Columna izquierda: Especificaciones del motor (Catálogo SQLite/MySQL o Manual),
        Diagnóstico IA & Métricas Físicas (arriba) y Explorador de Ensayos CSV (abajo).
      - Área central: Visor dual V(t) / I(t) + tarjeta inferior TRANSITORIO SRAM.
      - Área inferior: Consola.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import csv
import sys

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QDialog,
    QDockWidget,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

RAIZ_PROYECTO = Path(__file__).resolve().parents[2]

try:
    from src.core.ai_engine import (
        CANONICAL_STATES,
        DiagnosticReport,
        MotorDiagnosticAI,
    )
    from src.core.db_manager import DatabaseManager
    from src.core.motor_profile import MotorSpecProfile
    from src.core.transient_processor import (
        TransientAnalysisResult,
        TransientProcessor,
    )
    from src.ui.cad_theme import SEVERITY_STYLES, STATE_LABELS_ES
    from src.ui.motor_spec_panel import MotorSpecPanel
    from src.ui.transient_canvas import TransientCanvas
except ImportError:
    sys.path.append(str(RAIZ_PROYECTO))
    from src.core.ai_engine import (
        CANONICAL_STATES,
        DiagnosticReport,
        MotorDiagnosticAI,
    )
    from src.core.db_manager import DatabaseManager
    from src.core.motor_profile import MotorSpecProfile
    from src.core.transient_processor import (
        TransientAnalysisResult,
        TransientProcessor,
    )
    from src.ui.cad_theme import SEVERITY_STYLES, STATE_LABELS_ES
    from src.ui.motor_spec_panel import MotorSpecPanel
    from src.ui.transient_canvas import TransientCanvas


class MainWindow(QMainWindow):
    """Estación principal de análisis de transitorios, diagnóstico por IA y bitácora SQLite."""

    def __init__(
        self,
        processor: Optional[TransientProcessor] = None,
        ai_engine: Optional[MotorDiagnosticAI] = None,
        db_manager: Optional[DatabaseManager] = None,
        initial_paths: Optional[List[Path]] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.processor = processor if processor is not None else TransientProcessor()
        self.ai_engine = ai_engine if ai_engine is not None else MotorDiagnosticAI()
        self.db_manager = db_manager
        if self.db_manager is None:
            try:
                self.db_manager = DatabaseManager()
            except Exception:
                self.db_manager = None

        self.initial_paths = list(initial_paths) if initial_paths else []
        self._records: List[Tuple[TransientAnalysisResult, DiagnosticReport]] = []
        self._current_index: int = -1

        self.setWindowTitle(
            "Estación de Diagnóstico Inteligente de Motores DC — Expo ICE Impulsa 2026"
        )
        self.setDockOptions(
            QMainWindow.AnimatedDocks
            | QMainWindow.AllowNestedDocks
            | QMainWindow.AllowTabbedDocks
        )

        # Hacer que la consola inferior abarque todo el ancho de la ventana
        self.setCorner(Qt.BottomLeftCorner, Qt.BottomDockWidgetArea)
        self.setCorner(Qt.BottomRightCorner, Qt.BottomDockWidgetArea)

        self._build_central_workspace()
        self._build_docks()
        self._build_menus()
        self._build_toolbar()
        self._build_statusbar()
        self._apply_default_layout()

        self._log_console("Estación iniciada correctamente.")
        if self.ai_engine.classifier is not None:
            origen = self.ai_engine.training_metrics.get("origen", "MODELO_GUARDADO")
            self._log_console(f"Modelo IA cargado correctamente ({origen}).")
        if self.db_manager is not None:
            self._log_console(f"Base de datos SQLite activa: {self.db_manager.db_path.name}")

        QTimer.singleShot(80, self._load_startup_datasets)

    # ==========================================================================
    # CONSTRUCCIÓN DE ÁREA CENTRAL Y DOCKS
    # ==========================================================================
    def _build_central_workspace(self) -> None:
        self.canvas = TransientCanvas(self)
        self.setCentralWidget(self.canvas)

    def _build_docks(self) -> None:
        # ----------------------------------------------------------------------
        # 1. DOCK SUPERIOR IZQUIERDO: ESPECIFICACIONES MOTOR + DIAGNÓSTICO IA
        # ----------------------------------------------------------------------
        self.dock_diag = QDockWidget("Diagnóstico IA & Métricas Físicas", self)
        self.dock_diag.setObjectName("DockDiagnosticoIA")
        self.dock_diag.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.dock_diag.setMinimumWidth(340)

        diag_scroll = QScrollArea(self.dock_diag)
        diag_scroll.setWidgetResizable(True)
        diag_scroll.setFrameShape(QFrame.NoFrame)

        diag_container = QWidget()
        diag_layout = QVBoxLayout(diag_container)
        diag_layout.setContentsMargins(6, 6, 6, 6)
        diag_layout.setSpacing(8)

        # Panel opcional de especificaciones nominales del motor / Catálogo BD
        self.motor_panel = MotorSpecPanel(parent=diag_container)
        self.motor_panel.profile_changed.connect(self._on_motor_profile_changed)
        diag_layout.addWidget(self.motor_panel)

        # Tarjeta de estado principal
        self.diag_banner = QFrame()
        self.diag_banner.setObjectName("DiagBanner")
        banner_layout = QVBoxLayout(self.diag_banner)
        banner_layout.setContentsMargins(10, 8, 10, 8)
        banner_layout.setSpacing(3)

        self.lbl_diag_state = QLabel("ESTADO: EN ESPERA")
        self.lbl_diag_state.setFont(QFont("Segoe UI", 11, QFont.Bold))

        self.lbl_diag_desc = QLabel("Carga un ensayo CSV para diagnosticar")
        self.lbl_diag_desc.setFont(QFont("Segoe UI", 9))

        self.lbl_diag_meta = QLabel("Confianza: ---  |  Severidad: ---  |  Motor: ---")
        meta_font = QFont("Consolas")
        meta_font.setPointSizeF(8.5)
        self.lbl_diag_meta.setFont(meta_font)
        self.lbl_diag_meta.setWordWrap(True)

        banner_layout.addWidget(self.lbl_diag_state)
        banner_layout.addWidget(self.lbl_diag_desc)
        banner_layout.addWidget(self.lbl_diag_meta)
        diag_layout.addWidget(self.diag_banner)
        self._style_diag_banner("NEUTRO")

        # Probabilidades por escenario
        grp_probs = QGroupBox("Probabilidad por Escenario (IA)")
        probs_grid = QGridLayout(grp_probs)
        probs_grid.setContentsMargins(8, 12, 8, 8)
        probs_grid.setHorizontalSpacing(8)
        probs_grid.setVerticalSpacing(4)

        self._prob_bars: Dict[str, QProgressBar] = {}
        bar_label_font = QFont("Consolas", 8)
        bar_label_font.setBold(True)

        for row_idx, state_key in enumerate(CANONICAL_STATES):
            lbl_cls = QLabel(state_key)
            lbl_cls.setFont(bar_label_font)

            pbar = QProgressBar()
            pbar.setRange(0, 100)
            pbar.setValue(0)
            pbar.setFormat("%p%")

            probs_grid.addWidget(lbl_cls, row_idx, 0)
            probs_grid.addWidget(pbar, row_idx, 1)
            self._prob_bars[state_key] = pbar

        probs_grid.setColumnStretch(1, 1)
        diag_layout.addWidget(grp_probs)

        # Tabla de parámetros extraídos
        grp_params = QGroupBox("Parámetros Extraídos del Transitorio")
        params_layout = QVBoxLayout(grp_params)
        params_layout.setContentsMargins(6, 12, 6, 6)

        self.table_params = QTableWidget(0, 2)
        self.table_params.setHorizontalHeaderLabels(["Parámetro Físico", "Valor Medido"])
        self.table_params.verticalHeader().setVisible(False)
        self.table_params.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table_params.setSelectionMode(QTableWidget.NoSelection)
        self.table_params.setAlternatingRowColors(True)
        self.table_params.setMinimumHeight(145)
        self.table_params.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table_params.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeToContents
        )
        params_layout.addWidget(self.table_params)
        diag_layout.addWidget(grp_params)

        # Dictamen físico y recomendación
        grp_notes = QGroupBox("Dictamen Físico y Recomendación")
        notes_layout = QVBoxLayout(grp_notes)
        notes_layout.setContentsMargins(8, 12, 8, 8)
        self.lbl_diag_notes = QLabel("Sin dictamen generado.")
        self.lbl_diag_notes.setWordWrap(True)
        self.lbl_diag_notes.setTextInteractionFlags(Qt.TextSelectableByMouse)
        notes_layout.addWidget(self.lbl_diag_notes)
        diag_layout.addWidget(grp_notes)

        diag_layout.addStretch()
        diag_scroll.setWidget(diag_container)
        self.dock_diag.setWidget(diag_scroll)

        # ----------------------------------------------------------------------
        # 2. DOCK INFERIOR IZQUIERDO: EXPLORADOR DE ENSAYOS CSV
        # ----------------------------------------------------------------------
        self.dock_explorer = QDockWidget("Explorador de Ensayos CSV", self)
        self.dock_explorer.setObjectName("DockExploradorCSV")
        self.dock_explorer.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.dock_explorer.setMinimumWidth(340)

        exp_widget = QWidget()
        exp_layout = QVBoxLayout(exp_widget)
        exp_layout.setContentsMargins(6, 6, 6, 6)
        exp_layout.setSpacing(6)

        self.lbl_baseline_info = QLabel("Curva Base (Esc. 1): No definida")
        self.lbl_baseline_info.setWordWrap(True)
        self.lbl_baseline_info.setFont(QFont("Segoe UI", 8, QFont.Bold))
        exp_layout.addWidget(self.lbl_baseline_info)
        self._refresh_baseline_banner()

        self.table_datasets = QTableWidget(0, 4)
        self.table_datasets.setHorizontalHeaderLabels(
            ["Archivo", "Etiqueta", "Iss (A)", "Diagnóstico IA"]
        )
        self.table_datasets.verticalHeader().setVisible(False)
        self.table_datasets.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table_datasets.setSelectionBehavior(QTableWidget.SelectRows)
        self.table_datasets.setSelectionMode(QTableWidget.SingleSelection)
        self.table_datasets.setAlternatingRowColors(True)
        self.table_datasets.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table_datasets.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeToContents
        )
        self.table_datasets.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeToContents
        )
        self.table_datasets.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeToContents
        )
        self.table_datasets.currentCellChanged.connect(self._on_dataset_row_changed)
        exp_layout.addWidget(self.table_datasets, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        self.btn_set_base = QPushButton("Fijar Base")
        self.btn_set_base.setObjectName("PrimaryBtn")
        self.btn_set_base.setToolTip(
            "Establece el ensayo seleccionado como Curva Base sana (Escenario 1)"
        )
        self.btn_set_base.clicked.connect(self.set_selected_as_baseline)

        self.btn_remove_csv = QPushButton("Quitar")
        self.btn_remove_csv.setToolTip("Quita el ensayo seleccionado de la lista")
        self.btn_remove_csv.clicked.connect(self.remove_selected_dataset)

        self.btn_clear_csv = QPushButton("Limpiar")
        self.btn_clear_csv.setToolTip("Limpia todos los ensayos cargados en la sesión")
        self.btn_clear_csv.clicked.connect(self.clear_all_datasets)

        btn_row.addWidget(self.btn_set_base, stretch=1)
        btn_row.addWidget(self.btn_remove_csv, stretch=1)
        btn_row.addWidget(self.btn_clear_csv, stretch=1)
        exp_layout.addLayout(btn_row)

        self.dock_explorer.setWidget(exp_widget)

        # ----------------------------------------------------------------------
        # 3. DOCK INFERIOR: CONSOLA
        # ----------------------------------------------------------------------
        self.dock_console = QDockWidget("Consola", self)
        self.dock_console.setObjectName("DockConsola")
        self.dock_console.setAllowedAreas(Qt.BottomDockWidgetArea)

        console_widget = QWidget()
        console_layout = QVBoxLayout(console_widget)
        console_layout.setContentsMargins(4, 4, 4, 4)
        console_layout.setSpacing(0)

        self.txt_console = QPlainTextEdit()
        self.txt_console.setReadOnly(True)
        self.txt_console.setMaximumBlockCount(500)
        console_layout.addWidget(self.txt_console)

        self.dock_console.setWidget(console_widget)

        # Distribución inicial: 2 docks apilados a la izquierda + consola abajo
        self.addDockWidget(Qt.LeftDockWidgetArea, self.dock_diag)
        self.splitDockWidget(self.dock_diag, self.dock_explorer, Qt.Vertical)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.dock_console)

    def _apply_default_layout(self) -> None:
        """Ajusta proporciones y dimensiones de ventana para caber íntegramente en pantalla."""
        screen = QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            target_w = min(1520, int(avail.width() * 0.96))
            target_h = min(900, int(avail.height() * 0.94))
            self.resize(target_w, target_h)
            self.move(
                avail.x() + max(0, (avail.width() - target_w) // 2),
                avail.y() + max(0, (avail.height() - target_h) // 2),
            )
            left_w = max(350, min(430, int(target_w * 0.28)))
            diag_h = max(390, int(target_h * 0.56))
            exp_h = max(200, int(target_h * 0.24))
            console_h = max(95, min(130, int(target_h * 0.13)))
        else:
            self.resize(1366, 768)
            left_w, diag_h, exp_h, console_h = 380, 430, 210, 110

        self.resizeDocks([self.dock_diag], [left_w], Qt.Horizontal)
        self.resizeDocks([self.dock_diag, self.dock_explorer], [diag_h, exp_h], Qt.Vertical)
        self.resizeDocks([self.dock_console], [console_h], Qt.Vertical)
        self.showMaximized()

    def _restore_default_docks(self) -> None:
        """Restaura la posición y proporción original de los paneles."""
        for dock in (self.dock_diag, self.dock_explorer, self.dock_console):
            dock.setFloating(False)
            dock.show()

        self.addDockWidget(Qt.LeftDockWidgetArea, self.dock_diag)
        self.splitDockWidget(self.dock_diag, self.dock_explorer, Qt.Vertical)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.dock_console)
        self._apply_default_layout()
        self._log_console("Distribución de paneles restaurada.")

    # ==========================================================================
    # MENÚS, BARRA DE HERRAMIENTAS Y BARRA DE ESTADO
    # ==========================================================================
    def _build_menus(self) -> None:
        menubar = self.menuBar()

        # Menú Archivo
        menu_file = menubar.addMenu("Archivo")

        act_open_csv = QAction("Abrir CSV...", self)
        act_open_csv.setShortcut("Ctrl+O")
        act_open_csv.triggered.connect(self.open_csv_dialog)
        menu_file.addAction(act_open_csv)

        act_open_dir = QAction("Cargar Carpeta de Ensayos...", self)
        act_open_dir.setShortcut("Ctrl+Shift+O")
        act_open_dir.triggered.connect(self.open_folder_dialog)
        menu_file.addAction(act_open_dir)

        menu_file.addSeparator()

        act_exp_csv = QAction("Exportar Matriz de Características (.csv)...", self)
        act_exp_csv.setShortcut("Ctrl+E")
        act_exp_csv.triggered.connect(self.export_features_csv)
        menu_file.addAction(act_exp_csv)

        act_exp_txt = QAction("Exportar Reporte Técnico (.txt)...", self)
        act_exp_txt.setShortcut("Ctrl+R")
        act_exp_txt.triggered.connect(self.export_report_txt)
        menu_file.addAction(act_exp_txt)

        act_exp_png = QAction("Exportar Gráfica Dual (.png)...", self)
        act_exp_png.setShortcut("Ctrl+G")
        act_exp_png.triggered.connect(self.canvas.export_png)
        menu_file.addAction(act_exp_png)

        menu_file.addSeparator()

        act_exit = QAction("Salir", self)
        act_exit.setShortcut("Ctrl+Q")
        act_exit.triggered.connect(self.close)
        menu_file.addAction(act_exit)

        # Menú Diagnóstico e IA
        menu_ai = menubar.addMenu("Diagnóstico e IA")

        act_base = QAction("Fijar Selección como Curva Base (Esc. 1)", self)
        act_base.triggered.connect(self.set_selected_as_baseline)
        menu_ai.addAction(act_base)

        act_boot = QAction("Calibrar IA (Bootstrap RL)", self)
        act_boot.triggered.connect(self.run_bootstrap_calibration)
        menu_ai.addAction(act_boot)

        act_train = QAction("Entrenar IA (CSVs Reales)", self)
        act_train.triggered.connect(self.run_supervised_training)
        menu_ai.addAction(act_train)

        act_sync_bd = QAction("Recargar Catálogo de Motores (BD)", self)
        act_sync_bd.triggered.connect(lambda: self.motor_panel.sincronizar_catalogo(silencioso=False))
        menu_ai.addAction(act_sync_bd)

        act_hist_bd = QAction("Ver Historial de Diagnósticos (SQLite)...", self)
        act_hist_bd.triggered.connect(self.show_history_dialog)
        menu_ai.addAction(act_hist_bd)

        menu_ai.addSeparator()

        act_recalc = QAction("Re-evaluar Ensayo Actual", self)
        act_recalc.triggered.connect(self._rediagnose_all)
        menu_ai.addAction(act_recalc)

        # Menú Ver
        menu_view = menubar.addMenu("Ver")
        menu_view.addAction(self.dock_diag.toggleViewAction())
        menu_view.addAction(self.dock_explorer.toggleViewAction())
        menu_view.addAction(self.dock_console.toggleViewAction())
        menu_view.addSeparator()

        act_reset_view = QAction("Restaurar Distribución por Defecto", self)
        act_reset_view.triggered.connect(self._restore_default_docks)
        menu_view.addAction(act_reset_view)

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Herramientas Principales", self)
        toolbar.setObjectName("MainToolbar")
        toolbar.setMovable(False)
        self.addToolBar(Qt.TopToolBarArea, toolbar)

        actions = [
            ("Abrir CSV", self.open_csv_dialog),
            ("Cargar Carpeta", self.open_folder_dialog),
            ("Fijar como Base (Esc. 1)", self.set_selected_as_baseline),
            ("Calibrar IA (Bootstrap RL)", self.run_bootstrap_calibration),
            ("Entrenar IA (CSVs Reales)", self.run_supervised_training),
            ("Historial BD", self.show_history_dialog),
            ("Exportar Matriz CSV", self.export_features_csv),
            ("Exportar Reporte TXT", self.export_report_txt),
        ]
        for label, handler in actions:
            act = QAction(label, self)
            act.triggered.connect(handler)
            toolbar.addAction(act)

    def _build_statusbar(self) -> None:
        status = QStatusBar(self)
        self.setStatusBar(status)
        self.lbl_status_left = QLabel("Listo")
        self.lbl_status_right = QLabel()
        status.addWidget(self.lbl_status_left, stretch=1)
        status.addPermanentWidget(self.lbl_status_right)
        self._update_statusbar_ai()

    def _update_statusbar_ai(self) -> None:
        if self.ai_engine.classifier is not None:
            origen = self.ai_engine.training_metrics.get("origen", "ACTIVO")
            self.lbl_status_right.setText(f"Motor IA: ACTIVO ({origen})")
        else:
            self.lbl_status_right.setText("Motor IA: RESPALDO FÍSICO RL")

    # ==========================================================================
    # PERFIL NOMINAL DE MOTOR (PLACA / CATÁLOGO BD)
    # ==========================================================================
    def _on_motor_profile_changed(self, profile: MotorSpecProfile) -> None:
        self.ai_engine.set_motor_profile(profile)
        resumen = profile.resumen_corto()
        self._log_console(f"Perfil de motor actualizado: {resumen}")
        self._rediagnose_all()
        if not self._records:
            self.lbl_status_left.setText(f"Perfil activo: {resumen}")

    def _persistir_reporte_bd(self, report: DiagnosticReport) -> None:
        if self.db_manager is None:
            return
        try:
            perfil = self.ai_engine.motor_profile
            id_motor = perfil.id_motor if perfil is not None else None
            reg_id = self.db_manager.registrar_reporte(report, id_motor=id_motor)
            self._log_console(
                f"Bitácora SQLite: '{report.archivo}' registrado (ID #{reg_id})."
            )
        except Exception as exc:
            self._log_console(f"[AVISO BD] No se pudo guardar en historial: {exc}")

    def show_history_dialog(self) -> None:
        if self.db_manager is None:
            QMessageBox.warning(
                self, "Base de datos no disponible", "No hay instancia activa de DatabaseManager."
            )
            return

        registros = self.db_manager.obtener_historial(limite=100)
        dlg = QDialog(self)
        dlg.setWindowTitle("Bitácora Histórica de Diagnósticos (SQLite)")
        dlg.resize(960, 420)
        vlay = QVBoxLayout(dlg)

        tbl = QTableWidget(len(registros), 9)
        tbl.setHorizontalHeaderLabels(
            [
                "ID",
                "Fecha",
                "Motor",
                "Archivo CSV",
                "Diagnóstico",
                "Confianza",
                "Severidad",
                "Vss (V)",
                "Iss (A)",
            ]
        )
        tbl.verticalHeader().setVisible(False)
        tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        tbl.setSelectionBehavior(QTableWidget.SelectRows)
        tbl.setAlternatingRowColors(True)

        for r_idx, row in enumerate(registros):
            tbl.setItem(r_idx, 0, QTableWidgetItem(str(row.get("id_diagnostico", ""))))
            tbl.setItem(r_idx, 1, QTableWidgetItem(str(row.get("fecha_ensayo", ""))))
            tbl.setItem(r_idx, 2, QTableWidgetItem(str(row.get("motor", ""))))
            tbl.setItem(r_idx, 3, QTableWidgetItem(str(row.get("archivo_csv", ""))))
            tbl.setItem(r_idx, 4, QTableWidgetItem(str(row.get("estado_detectado", ""))))
            tbl.setItem(r_idx, 5, QTableWidgetItem(f"{row.get('confianza_pct', 0.0):.1f}%"))
            tbl.setItem(r_idx, 6, QTableWidgetItem(str(row.get("severidad", ""))))
            vss = row.get("v_ss_v")
            iss = row.get("i_ss_a")
            tbl.setItem(r_idx, 7, QTableWidgetItem(f"{vss:.2f}" if vss is not None else "---"))
            tbl.setItem(r_idx, 8, QTableWidgetItem(f"{iss:.3f}" if iss is not None else "---"))

        tbl.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        vlay.addWidget(tbl)

        btn_close = QPushButton("Cerrar")
        btn_close.clicked.connect(dlg.accept)
        vlay.addWidget(btn_close, alignment=Qt.AlignRight)
        dlg.exec_()

    # ==========================================================================
    # GESTIÓN DE DATASETS Y ACCIONES DE USUARIO
    # ==========================================================================
    def _load_startup_datasets(self) -> None:
        paths_to_load: List[Path] = list(self.initial_paths)
        if not paths_to_load:
            raw_dir = RAIZ_PROYECTO / "datasets" / "raw"
            if raw_dir.is_dir():
                paths_to_load.extend(sorted(raw_dir.glob("*.csv")))
            for candidate in [
                RAIZ_PROYECTO / "dataset_Motor_prueba_12V.csv",
                RAIZ_PROYECTO / "dataset_Motor_prueba_12V (1).csv",
            ]:
                if candidate.exists() and candidate not in paths_to_load:
                    paths_to_load.append(candidate)

        if paths_to_load:
            self.load_csv_files(paths_to_load)

    def open_csv_dialog(self) -> None:
        default_dir = RAIZ_PROYECTO / "datasets" / "raw"
        start_dir = str(default_dir if default_dir.exists() else RAIZ_PROYECTO)
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Seleccionar Archivos CSV de Transitorios",
            start_dir,
            "Archivos CSV (*.csv)",
        )
        if files:
            self.load_csv_files([Path(f) for f in files])

    def open_folder_dialog(self) -> None:
        default_dir = RAIZ_PROYECTO / "datasets" / "raw"
        start_dir = str(default_dir if default_dir.exists() else RAIZ_PROYECTO)
        folder = QFileDialog.getExistingDirectory(
            self,
            "Seleccionar Carpeta con Datasets CSV",
            start_dir,
        )
        if folder:
            csvs = sorted(Path(folder).glob("*.csv"))
            if not csvs:
                QMessageBox.information(
                    self, "Carpeta vacía", "No se encontraron archivos .csv en la carpeta."
                )
                return
            self.load_csv_files(csvs)

    def load_csv_files(self, paths: List[Path]) -> None:
        if isinstance(paths, (str, Path)):
            normalized_paths = [Path(paths)]
        else:
            normalized_paths = [Path(p) for p in paths]

        loaded_count = 0
        for path in normalized_paths:
            try:
                res = self.processor.analyze_csv(path)
                if self.ai_engine.baseline_result is None and any(
                    k in res.label.upper() for k in ("NORMAL", "SANO", "NUEVO", "BASE", "ESC1")
                ):
                    self.ai_engine.set_baseline(res)

                report = self.ai_engine.diagnose(res)

                existing_idx = next(
                    (
                        idx
                        for idx, (r_old, _) in enumerate(self._records)
                        if r_old.source_name == res.source_name
                    ),
                    None,
                )
                if existing_idx is not None:
                    self._records[existing_idx] = (res, report)
                else:
                    self._records.append((res, report))

                loaded_count += 1
                self._log_console(
                    f"Procesado '{res.source_name}' [{res.label}] -> "
                    f"{report.estado_detectado} ({report.confianza_pct:.1f}%) | Fs={res.fs_real_hz:.0f} Hz"
                )
                self._persistir_reporte_bd(report)
            except Exception as exc:
                self._log_console(f"[ERROR] No se pudo procesar '{Path(path).name}': {exc}")

        if loaded_count > 0:
            self._refresh_baseline_banner()
            self._refresh_dataset_table()
            target_row = max(0, len(self._records) - 1) if self._current_index < 0 else self._current_index
            self.table_datasets.selectRow(target_row)
            self._display_record(target_row)

    def set_selected_as_baseline(self) -> None:
        if not (0 <= self._current_index < len(self._records)):
            QMessageBox.information(
                self, "Sin selección", "Selecciona primero un ensayo CSV en el explorador."
            )
            return

        res, _ = self._records[self._current_index]
        self.ai_engine.set_baseline(res)
        self._log_console(
            f"Curva Base (Esc. 1) fijada: '{res.source_name}' "
            f"(Vss={res.v_ss_v:.2f} V, Iss={res.i_ss_ma:.0f} mA)."
        )
        self._rediagnose_all()

    def run_bootstrap_calibration(self) -> None:
        baseline = self.ai_engine.baseline_result
        if baseline is None and 0 <= self._current_index < len(self._records):
            baseline = self._records[self._current_index][0]
            self.ai_engine.set_baseline(baseline)

        if baseline is None:
            QMessageBox.warning(
                self,
                "Curva Base requerida",
                "Carga y selecciona primero un ensayo sano (Escenario 1) para calibrar el modelo RL.",
            )
            return

        try:
            metrics = self.ai_engine.bootstrap_from_baseline(
                baseline, samples_per_class=30, save_after_train=True
            )
            self._log_console(
                f"Calibración Bootstrap RL completada: Rw={metrics.get('rw_estimado_ohm')} Ω, "
                f"Rm(∞)={metrics.get('r_minf_estimado_ohm')} Ω, "
                f"Consistencia={metrics.get('cv_consistencia_sintetica_pct')}%."
            )
            self._update_statusbar_ai()
            self._rediagnose_all()
        except Exception as exc:
            QMessageBox.critical(self, "Error en Calibración RL", str(exc))

    def run_supervised_training(self) -> None:
        if len(self._records) < 2:
            QMessageBox.warning(
                self,
                "Datos insuficientes",
                "Carga al menos 2 escenarios distintos etiquetados en CSV para entrenar la IA.",
            )
            return

        try:
            results_only = [r for r, _ in self._records]
            metrics = self.ai_engine.train_from_results(results_only, save_after_train=True)
            self._log_console(
                f"Entrenamiento supervisado completado ({metrics.get('total_muestras')} muestras, "
                f"CV={metrics.get('cv_accuracy_pct')}%)."
            )
            self._update_statusbar_ai()
            self._rediagnose_all()
        except Exception as exc:
            QMessageBox.warning(self, "Entrenamiento Supervisado", str(exc))

    def remove_selected_dataset(self) -> None:
        row = self.table_datasets.currentRow()
        if not (0 <= row < len(self._records)):
            return
        removed_name = self._records[row][0].source_name
        self._records.pop(row)
        self._log_console(f"Ensayo '{removed_name}' removido de la sesión.")

        self._refresh_dataset_table()
        if self._records:
            new_row = min(row, len(self._records) - 1)
            self.table_datasets.selectRow(new_row)
            self._display_record(new_row)
        else:
            self._current_index = -1
            self._clear_diagnostic_view()
            self.canvas.clear_plot()

    def clear_all_datasets(self) -> None:
        self._records.clear()
        self._current_index = -1
        self._refresh_dataset_table()
        self._clear_diagnostic_view()
        self.canvas.clear_plot()
        self._log_console("Lista de ensayos limpiada.")

    # ==========================================================================
    # ACTUALIZACIÓN DE INTERFAZ Y DIAGNÓSTICO
    # ==========================================================================
    def _rediagnose_all(self) -> None:
        self._refresh_baseline_banner()
        updated: List[Tuple[TransientAnalysisResult, DiagnosticReport]] = []
        for res, _ in self._records:
            updated.append((res, self.ai_engine.diagnose(res)))
        self._records = updated
        self._refresh_dataset_table()

        if 0 <= self._current_index < len(self._records):
            self.table_datasets.selectRow(self._current_index)
            self._display_record(self._current_index)

    def _on_dataset_row_changed(
        self, current_row: int, _curr_col: int, _prev_row: int, _prev_col: int
    ) -> None:
        if 0 <= current_row < len(self._records):
            self._display_record(current_row)

    def _display_record(self, index: int) -> None:
        if not (0 <= index < len(self._records)):
            return
        self._current_index = index
        res, report = self._records[index]

        # Actualizar visor central
        self.canvas.plot_analysis(
            res=res,
            report=report,
            baseline=self.ai_engine.baseline_result,
        )

        # Actualizar banner de estado IA
        self._style_diag_banner(report.severidad)
        self.lbl_diag_state.setText(f"ESTADO: {report.estado_detectado}")
        self.lbl_diag_desc.setText(
            STATE_LABELS_ES.get(report.estado_detectado, report.estado_detectado)
        )
        self.lbl_diag_meta.setText(
            f"Confianza: {report.confianza_pct:.1f}%  |  "
            f"Severidad: {report.severidad}  |  Motor: {report.metodo_inferencia}"
        )

        # Actualizar barras de probabilidad
        for state_key, pbar in self._prob_bars.items():
            pct = int(round(report.probabilidades_clase.get(state_key, 0.0) * 100.0))
            pbar.setValue(pct)

        # Actualizar tabla de parámetros extraídos
        feat = report.parametros_clave
        filas_metricas = [
            ("Voltaje estable (Vss)", f"{res.v_ss_v:.3f} V"),
            ("Corriente pico suave", f"{res.i_pico_suave_ma:.1f} mA ({res.i_pico_suave_ma/1000.0:.3f} A)"),
            ("Corriente pico crudo", f"{res.i_pico_crudo_ma:.1f} mA ({res.i_pico_crudo_ma/1000.0:.3f} A)"),
            ("Corriente estable (Iss)", f"{res.i_ss_ma:.1f} mA ({res.i_ss_ma/1000.0:.3f} A)"),
            ("Tiempo al pico (tpico)", f"{res.t_pico_ms:.1f} ms"),
            (
                "Tiempo estabilización",
                f"{res.t_estab_ms:.0f} ms ({res.t_estab_ms/1000.0:.3f} s)"
                if res.t_estab_ms is not None
                else "No estabilizado",
            ),
            ("Constante de tiempo (τ)", f"{res.tau_ms:.2f} ms"),
            ("Potencia máxima (Pmax)", f"{res.p_max_w:.2f} W"),
            ("Potencia estable (Pss)", f"{res.p_ss_w:.2f} W"),
            ("Energía de arranque", f"{res.e_arranque_j:.3f} J"),
            ("Relación Ipico / Iss", f"{feat.get('ratio_pico_ss', 0.0):.2f} x"),
            ("Caída transitoria", f"{feat.get('caida_transitoria_pct', 0.0):.1f} %"),
            ("R devanado est. (Rw)", f"{feat.get('r_devanado_est_ohm', 0.0):.2f} Ω"),
            ("R aparente estable", f"{feat.get('r_aparente_ss_ohm', 0.0):.2f} Ω"),
            ("Factor FCEM", f"{feat.get('factor_fcem', 0.0):.3f}"),
            ("Muestreo efectivo (Fs)", f"{res.fs_real_hz:.1f} Hz (Δt={res.dt_med_us:.0f} µs)"),
        ]

        self.table_params.setRowCount(len(filas_metricas))
        for r_idx, (nombre, valor) in enumerate(filas_metricas):
            item_k = QTableWidgetItem(nombre)
            item_v = QTableWidgetItem(valor)
            val_font = QFont("Consolas", 9)
            val_font.setBold(True)
            item_v.setFont(val_font)
            self.table_params.setItem(r_idx, 0, item_k)
            self.table_params.setItem(r_idx, 1, item_v)

        # Actualizar dictamen físico
        sintomas_html = "<br>".join(f"• {s}" for s in report.sintomas_detectados)
        self.lbl_diag_notes.setText(
            f"{sintomas_html}<br><br><b>Acción:</b> {report.recomendacion_tecnica}"
        )

        perfil_sufijo = (
            f" | Perfil: {self.ai_engine.motor_profile.modelo}"
            if self.ai_engine.motor_profile is not None
            and self.ai_engine.motor_profile.tiene_datos()
            else ""
        )
        self.lbl_status_left.setText(
            f"Activo: {res.source_name} | Muestras: {res.n_active_samples} | "
            f"Diagnóstico: {report.estado_detectado} ({report.confianza_pct:.1f}%){perfil_sufijo}"
        )

    def _clear_diagnostic_view(self) -> None:
        self._style_diag_banner("NEUTRO")
        self.lbl_diag_state.setText("ESTADO: EN ESPERA")
        self.lbl_diag_desc.setText("Carga un ensayo CSV para diagnosticar")
        self.lbl_diag_meta.setText("Confianza: ---  |  Severidad: ---  |  Motor: ---")
        for pbar in self._prob_bars.values():
            pbar.setValue(0)
        self.table_params.setRowCount(0)
        self.lbl_diag_notes.setText("Sin dictamen generado.")
        self.lbl_status_left.setText("Listo")

    def _refresh_dataset_table(self) -> None:
        self.table_datasets.blockSignals(True)
        self.table_datasets.setRowCount(len(self._records))
        for row_idx, (res, report) in enumerate(self._records):
            self.table_datasets.setItem(row_idx, 0, QTableWidgetItem(res.source_name))
            self.table_datasets.setItem(row_idx, 1, QTableWidgetItem(res.label))
            self.table_datasets.setItem(
                row_idx, 2, QTableWidgetItem(f"{res.i_ss_ma / 1000.0:.3f}")
            )
            self.table_datasets.setItem(
                row_idx,
                3,
                QTableWidgetItem(f"{report.estado_detectado} ({report.confianza_pct:.0f}%)"),
            )
        self.table_datasets.blockSignals(False)

    def _refresh_baseline_banner(self) -> None:
        base = self.ai_engine.baseline_result
        if base is not None:
            nota_origen = (
                " [restaurada de modelo]"
                if not any(r.source_name == base.source_name for r, _ in self._records)
                and self.ai_engine.classifier is not None
                else ""
            )
            self.lbl_baseline_info.setText(
                f"Curva Base (Esc. 1): {base.source_name} "
                f"(Vss={base.v_ss_v:.2f}V, Iss={base.i_ss_ma:.0f}mA{nota_origen})"
            )
            self.lbl_baseline_info.setStyleSheet(
                "background-color: #dcfce7; color: #14532d; border: 1px solid #16a34a; "
                "border-radius: 3px; padding: 5px 7px;"
            )
        else:
            self.lbl_baseline_info.setText(
                "Curva Base (Esc. 1): No definida — selecciona un ensayo sano"
            )
            self.lbl_baseline_info.setStyleSheet(
                "background-color: #e2e8f0; color: #334155; border: 1px solid #94a3b8; "
                "border-radius: 3px; padding: 5px 7px;"
            )

    def _style_diag_banner(self, severidad: str) -> None:
        st = SEVERITY_STYLES.get(severidad, SEVERITY_STYLES["NEUTRO"])
        self.diag_banner.setStyleSheet(
            f"QFrame#DiagBanner {{ background-color: {st['bg']}; "
            f"border: 1px solid {st['border']}; border-radius: 4px; }}"
        )
        self.lbl_diag_state.setStyleSheet(f"color: {st['text']};")
        self.lbl_diag_desc.setStyleSheet(f"color: {st['text']};")
        self.lbl_diag_meta.setStyleSheet(f"color: {st['text']};")

    # ==========================================================================
    # EXPORTACIÓN Y CONSOLA
    # ==========================================================================
    def export_features_csv(self) -> None:
        if not self._records:
            QMessageBox.information(
                self, "Sin datos", "No hay ensayos procesados para exportar."
            )
            return

        default_dir = RAIZ_PROYECTO / "datasets" / "processed"
        default_dir.mkdir(parents=True, exist_ok=True)
        filepath, _ = QFileDialog.getSaveFileName(
            self,
            "Exportar Matriz de Características",
            str(default_dir / "matriz_caracteristicas_motores.csv"),
            "Archivo CSV (*.csv)",
        )
        if not filepath:
            return

        perfil = self.ai_engine.motor_profile
        perfil_str = (
            perfil.resumen_corto()
            if (perfil is not None and perfil.tiene_datos())
            else "No especificado"
        )

        rows: List[Dict[str, Any]] = []
        for res, report in self._records:
            row = {
                "archivo": res.source_name,
                "etiqueta_csv": res.label,
                "perfil_motor": perfil_str,
                "diagnostico_ia": report.estado_detectado,
                "confianza_pct": report.confianza_pct,
                "severidad": report.severidad,
            }
            row.update(report.parametros_clave)
            rows.append(row)

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

        self._log_console(f"Matriz de características exportada en '{filepath}'.")

    def export_report_txt(self) -> None:
        if not (0 <= self._current_index < len(self._records)):
            QMessageBox.information(
                self, "Sin selección", "Selecciona un ensayo para exportar su reporte técnico."
            )
            return

        res, report = self._records[self._current_index]
        default_name = f"dictamen_{Path(res.source_name).stem}.txt"
        filepath, _ = QFileDialog.getSaveFileName(
            self,
            "Exportar Reporte Técnico",
            default_name,
            "Archivo de Texto (*.txt)",
        )
        if not filepath:
            return

        contenido = (
            "========================================================================\n"
            "  ESTACIÓN DE DIAGNÓSTICO INTELIGENTE DE MOTORES DC — EXPO ICE 2026\n"
            "========================================================================\n"
            f"Archivo analizado : {res.source_name}\n"
            f"Etiqueta original : {res.label}\n"
            f"Fecha de emisión  : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"{report.formatear_reporte_texto()}\n"
        )
        Path(filepath).write_text(contenido, encoding="utf-8")
        self._log_console(f"Reporte técnico guardado en '{filepath}'.")

    def _log_console(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.txt_console.appendPlainText(f"[{stamp}] {message}")