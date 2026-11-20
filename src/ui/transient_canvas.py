"""
Módulo: src/ui/transient_canvas.py
Proyecto: Diagnóstico Inteligente de Motores DC - Expo ICE Impulsa 2026
Descripción:
    Widget de graficación Matplotlib incrustado en PyQt5:
      - Distribución mitad y mitad limpia: Voltaje V(t) [V] | Corriente I(t) [mA]
      - Anclaje estricto en t = 0.0 s e y = 0.0 (sin mostrar pre-trigger)
      - Superposición Crudo SRAM + Suavizado Gaussiano + Curva Base opcional
      - Cuadro dedicado de telemetría "TRANSITORIO SRAM" fuera del área de trazado
        para evitar cualquier obstrucción sobre las curvas.
"""

from pathlib import Path
from typing import Dict, Optional
import sys

import numpy as np
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure
import numpy as np
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QVBoxLayout, QWidget
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure


class AnimatedTransientCanvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.fig = Figure(figsize=(11, 5.2), facecolor="#12161f")
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        self.toolbar.setStyleSheet("background-color: #1c2230; color: #e2e8f0;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas)

        self.ax_v = self.fig.add_subplot(121)
        self.ax_i = self.fig.add_subplot(122)
        self._configurar_ejes()

        self.linea_v_raw, = self.ax_v.plot([], [], color="#f87171", lw=0.6, alpha=0.35, label="Crudo SRAM")
        self.linea_v, = self.ax_v.plot([], [], color="#ef4444", lw=2.2, label="V(t) Suavizado")

        self.linea_i_raw, = self.ax_i.plot([], [], color="#60a5fa", lw=0.6, alpha=0.35, label="Crudo SRAM")
        self.linea_i, = self.ax_i.plot([], [], color="#3b82f6", lw=2.2, label="I(t) Suavizado")

        self.vline_i = self.ax_i.axvline(x=0, color="#94a3b8", ls="--", lw=0.9, visible=False)
        self.hline_i = self.ax_i.axhline(y=0, color="#94a3b8", ls="--", lw=0.9, visible=False)
        self.annot = self.ax_i.annotate(
            "", xy=(0, 0), xytext=(12, 12), textcoords="offset points",
            bbox=dict(boxstyle="round,pad=0.4", fc="#1e293b", ec="#38bdf8", lw=1),
            color="#f8fafc", fontsize=8.5, visible=False
        )

        self.timer_anim = QTimer(self)
        self.timer_anim.setInterval(16)  # ~60 FPS
        self.timer_anim.timeout.connect(self._avanzar_cuadro)

        self.t_full = np.array([])
        self.v_full = np.array([])
        self.i_full = np.array([])
        self.v_raw_full = None
        self.i_raw_full = None
        self.metricas_pendientes = None
        self.idx_anim = 0
        self.paso_anim = 25
        self.animando = False

        self.canvas.mpl_connect("motion_notify_event", self._on_mouse_move)
        self.canvas.mpl_connect("scroll_event", self._on_scroll_zoom)

    def _configurar_ejes(self):
        for ax, titulo, ylabel in [
            (self.ax_v, "Voltaje V(t)", "Voltaje [V]"),
            (self.ax_i, "Corriente I(t)", "Corriente [mA]")
        ]:
            ax.set_facecolor("#0f131a")
            ax.set_title(titulo, color="#e2e8f0", fontweight="bold")
            ax.set_xlabel("Tiempo desde arranque, t [s]", color="#94a3b8")
            ax.set_ylabel(ylabel, color="#94a3b8")
            ax.tick_params(colors="#94a3b8")
            ax.grid(True, linestyle=":", color="#2d3748", alpha=0.8)

    def iniciar_grafica_animada(self, t, v_smooth, i_smooth, v_raw=None, i_raw=None,
                                metricas=None, duracion_anim_s=1.4):
        self.timer_anim.stop()
        self.t_full = np.asarray(t)
        self.v_full = np.asarray(v_smooth)
        self.i_full = np.asarray(i_smooth)
        self.v_raw_full = np.asarray(v_raw) if v_raw is not None else None
        self.i_raw_full = np.asarray(i_raw) if i_raw is not None else None
        self.metricas_pendientes = metricas

        while len(self.ax_v.lines) > 2:
            self.ax_v.lines[-1].remove()
        while len(self.ax_i.lines) > 2:
            self.ax_i.lines[-1].remove()

        self.vline_i.set_visible(False)
        self.hline_i.set_visible(False)
        self.annot.set_visible(False)

        t_max = max(0.05, float(self.t_full[-1]))
        v_max = max(1.0, float(np.max(self.v_full)) * 1.18)
        i_max = max(100.0, float(np.max(self.i_full)) * 1.15)

        self.ax_v.set_xlim(0.0, t_max)
        self.ax_i.set_xlim(0.0, t_max)
        self.ax_v.set_ylim(0.0, v_max)
        self.ax_i.set_ylim(0.0, i_max)

        self.ax_v.set_title("Voltaje V(t) — Trazando transitorio...", color="#38bdf8", fontweight="bold")
        self.ax_i.set_title("Corriente I(t) — Trazando transitorio...", color="#38bdf8", fontweight="bold")

        total_cuadros = max(15, int((duracion_anim_s * 1000) / self.timer_anim.interval()))
        self.paso_anim = max(1, len(self.t_full) // total_cuadros)
        self.idx_anim = self.paso_anim
        self.animando = True
        self.timer_anim.start()

    def _avanzar_cuadro(self):
        if self.idx_anim >= len(self.t_full):
            self.idx_anim = len(self.t_full)
            self._actualizar_trazo(self.idx_anim)
            self.timer_anim.stop()
            self.animando = False
            self._finalizar_y_dibujar_marcadores()
            return

        self._actualizar_trazo(self.idx_anim)
        self.idx_anim += self.paso_anim

    def _actualizar_trazo(self, n):
        t_slice = self.t_full[:n]
        self.linea_v.set_data(t_slice, self.v_full[:n])
        self.linea_i.set_data(t_slice, self.i_full[:n])

        if self.v_raw_full is not None and len(self.v_raw_full) == len(self.t_full):
            self.linea_v_raw.set_data(t_slice, self.v_raw_full[:n])
        if self.i_raw_full is not None and len(self.i_raw_full) == len(self.t_full):
            self.linea_i_raw.set_data(t_slice, self.i_raw_full[:n])

        self.canvas.draw_idle()

    def _finalizar_y_dibujar_marcadores(self):
        if self.metricas_pendientes:
            m = self.metricas_pendientes
            t_pico = m.get("t_pico_s", 0.0)
            i_pico = m.get("I_pico_suave_mA", 0.0)
            i_ss = m.get("I_estable_mA", 0.0)
            v_ss = m.get("V_estable_V", 0.0)
            tau_ms = m.get("tau_ms", 0.0)
            t_tau = t_pico + (tau_ms / 1000.0)
            i_tau = i_pico - 0.632 * (i_pico - i_ss)

            self.ax_i.plot(t_pico, i_pico, "o", color="#facc15", markersize=6, label=f"Pico: {i_pico:.1f} mA")
            if tau_ms > 0:
                self.ax_i.plot(t_tau, i_tau, "o", color="#22c55e", markersize=6, label=f"63.2% (τ = {tau_ms:.1f} ms)")
                self.ax_i.axvline(t_tau, color="#22c55e", ls="--", lw=1.1, alpha=0.8)

            self.ax_i.axhline(i_ss, color="#a855f7", ls=":", lw=1.3, label=f"Iss = {i_ss:.1f} mA")
            self.ax_v.axhline(v_ss, color="#f43f5e", ls=":", lw=1.3, label=f"Vss = {v_ss:.2f} V")

            self.ax_v.set_title(f"Voltaje Estable (Vss = {v_ss:.2f} V)", color="#4ade80", fontweight="bold")
            self.ax_i.set_title(f"Transitorio Estabilizado (τ = {tau_ms:.2f} ms)", color="#4ade80", fontweight="bold")
        else:
            self.ax_v.set_title("Voltaje V(t) — Completado", color="#4ade80", fontweight="bold")
            self.ax_i.set_title("Corriente I(t) — Completado", color="#4ade80", fontweight="bold")

        self.ax_v.legend(loc="lower right", facecolor="#1e293b", labelcolor="white", fontsize=8)
        self.ax_i.legend(loc="upper right", facecolor="#1e293b", labelcolor="white", fontsize=8)
        self.canvas.draw_idle()

    def _on_mouse_move(self, event):
        if self.animando or event.inaxes != self.ax_i or len(self.t_full) == 0 or event.xdata is None:
            if self.annot.get_visible():
                self.vline_i.set_visible(False)
                self.hline_i.set_visible(False)
                self.annot.set_visible(False)
                self.canvas.draw_idle()
            return

        idx = int(np.argmin(np.abs(self.t_full - event.xdata)))
        tx, iy, vy = self.t_full[idx], self.i_full[idx], self.v_full[idx]

        self.vline_i.set_xdata([tx])
        self.hline_i.set_ydata([iy])
        self.vline_i.set_visible(True)
        self.hline_i.set_visible(True)

        self.annot.xy = (tx, iy)
        self.annot.set_text(f"t = {tx*1000:.1f} ms\nI = {iy:.1f} mA\nV = {vy:.2f} V")
        self.annot.set_visible(True)
        self.canvas.draw_idle()

    def _on_scroll_zoom(self, event):
        if self.animando or event.inaxes not in (self.ax_v, self.ax_i) or event.xdata is None:
            return
        ax = event.inaxes
        factor = 0.8 if event.button == "up" else 1.25
        xlim = ax.get_xlim()
        new_width = (xlim[1] - xlim[0]) * factor
        rel_x = (event.xdata - xlim[0]) / (xlim[1] - xlim[0])
        ax.set_xlim(max(0.0, event.xdata - new_width * rel_x), event.xdata + new_width * (1 - rel_x))
        self.canvas.draw_idle()

try:
    from src.core.transient_processor import TransientAnalysisResult
    from src.core.ai_engine import DiagnosticReport
except ImportError:
    sys.path.append(str(Path(__file__).resolve().parents[2]))
    from src.core.transient_processor import TransientAnalysisResult
    from src.core.ai_engine import DiagnosticReport


class TransientCanvas(QWidget):
    """Visor dual de transitorios SRAM con barra de herramientas CAD/CAE y tarjeta inferior."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._current_result: Optional[TransientAnalysisResult] = None
        self._current_report: Optional[DiagnosticReport] = None
        self._current_baseline: Optional[TransientAnalysisResult] = None
        self._metric_labels: Dict[str, QLabel] = {}

        self._build_ui()
        self._init_empty_axes()
        self._reset_sram_panel()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # --- Barra superior de controles ---
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(2, 0, 2, 0)

        self.fig = Figure(figsize=(14, 5.5), dpi=100)
        self.fig.patch.set_facecolor("#f0f2f5")
        self.canvas = FigureCanvas(self.fig)
        self.toolbar = NavigationToolbar(self.canvas, self)
        self.toolbar.setStyleSheet("background: transparent; border: none;")

        top_bar.addWidget(self.toolbar)
        top_bar.addStretch()

        self.chk_overlay_baseline = QCheckBox("Superponer Curva Base (Esc. 1)")
        self.chk_overlay_baseline.setChecked(True)
        self.chk_overlay_baseline.setEnabled(False)
        self.chk_overlay_baseline.setToolTip(
            "Dibuja en línea discontinua la curva temporal del ensayo base (Escenario 1) "
            "cuando está cargado en la sesión actual."
        )
        self.chk_overlay_baseline.toggled.connect(self._replot_current)
        top_bar.addWidget(self.chk_overlay_baseline)

        self.btn_save_png = QPushButton("Guardar Gráfica PNG")
        self.btn_save_png.setToolTip("Exporta la gráfica dual actual limpia en alta resolución (300 DPI)")
        self.btn_save_png.clicked.connect(self.export_png)
        top_bar.addWidget(self.btn_save_png)

        layout.addLayout(top_bar)
        layout.addWidget(self.canvas, stretch=1)

        # --- Cuadro dedicado de Telemetría SRAM fuera del área de curvas ---
        self._build_sram_panel(layout)

        self.ax_v, self.ax_i = self.fig.subplots(1, 2)
        self.fig.subplots_adjust(left=0.06, right=0.98, top=0.88, bottom=0.13, wspace=0.22)

    def _build_sram_panel(self, parent_layout: QVBoxLayout) -> None:
        """Crea el panel inferior dedicado para las métricas de TRANSITORIO SRAM."""
        self.sram_frame = QFrame(self)
        self.sram_frame.setObjectName("SramTelemetryCard")
        self.sram_frame.setStyleSheet(
            """
            QFrame#SramTelemetryCard {
                background-color: #f8fafc;
                border: 1px solid #2b5c8f;
                border-radius: 6px;
            }
            QLabel#SramHeader {
                color: #1e3a5f;
                font-weight: bold;
                padding-bottom: 2px;
                border-bottom: 1px solid #cbd5e1;
            }
            QLabel.SramKey {
                color: #475569;
                font-weight: bold;
            }
            QLabel.SramVal {
                color: #0f172a;
            }
            """
        )

        card_layout = QVBoxLayout(self.sram_frame)
        card_layout.setContentsMargins(10, 6, 10, 8)
        card_layout.setSpacing(6)

        self.lbl_sram_header = QLabel("TRANSITORIO SRAM (Sin datos)")
        self.lbl_sram_header.setObjectName("SramHeader")
        self.lbl_sram_header.setFont(QFont("Consolas", 10))
        card_layout.addWidget(self.lbl_sram_header)

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(4)

        campos = [
            ("estab", "Estabilización:", 0, 0),
            ("ipico_crudo", "I pico crudo:", 1, 0),
            ("ipico_suave", "I pico suave:", 2, 0),
            ("iss", "I estable (Iss):", 0, 2),
            ("vss", "V estable (Vss):", 1, 2),
            ("tau", "Constante τ:", 2, 2),
            ("potencia", "Potencia máx.:", 0, 4),
            ("energia", "Energía arranq.:", 1, 4),
            ("muestreo", "Muestreo CSV:", 2, 4),
            ("diagnostico", "DIAGNÓSTICO IA:", 0, 6),
        ]

        mono_font = QFont("Consolas", 9)
        for key, label_txt, row, col in campos:
            lbl_k = QLabel(label_txt)
            lbl_k.setProperty("class", "SramKey")
            lbl_k.setFont(mono_font)

            lbl_v = QLabel("---")
            lbl_v.setProperty("class", "SramVal")
            lbl_v.setFont(mono_font)
            lbl_v.setTextInteractionFlags(Qt.TextSelectableByMouse)

            grid.addWidget(lbl_k, row, col)
            grid.addWidget(lbl_v, row, col + 1)
            self._metric_labels[key] = lbl_v

        grid.setColumnStretch(7, 1)
        card_layout.addLayout(grid)
        parent_layout.addWidget(self.sram_frame, stretch=0)

    def _reset_sram_panel(self) -> None:
        self.lbl_sram_header.setText("TRANSITORIO SRAM (En espera de captura o archivo CSV)")
        for lbl in self._metric_labels.values():
            lbl.setText("---")
            lbl.setStyleSheet("color: #0f172a;")

    def _update_sram_panel(
        self, res: TransientAnalysisResult, report: Optional[DiagnosticReport]
    ) -> None:
        estab_txt = f"{res.t_estab_ms:.0f} ms" if res.t_estab_ms is not None else "Fin búfer"
        self.lbl_sram_header.setText(
            f"TRANSITORIO SRAM ({res.n_active_samples} pts) — Archivo: {res.source_name}"
        )

        self._metric_labels["estab"].setText(f"{estab_txt} (corte {res.t_corte_ms:.0f} ms)")
        self._metric_labels["ipico_crudo"].setText(
            f"{res.i_pico_crudo_ma:.1f} mA ({res.i_pico_crudo_ma / 1000.0:.2f} A)"
        )
        self._metric_labels["ipico_suave"].setText(
            f"{res.i_pico_suave_ma:.1f} mA ({res.i_pico_suave_ma / 1000.0:.2f} A)"
        )
        self._metric_labels["iss"].setText(
            f"{res.i_ss_ma:.1f} mA ({res.i_ss_ma / 1000.0:.2f} A)"
        )
        self._metric_labels["vss"].setText(f"{res.v_ss_v:.2f} V")
        self._metric_labels["tau"].setText(f"{res.tau_ms:.2f} ms")
        self._metric_labels["potencia"].setText(
            f"{res.p_max_w:.2f} W (Pss = {res.p_ss_w:.2f} W)"
        )
        self._metric_labels["energia"].setText(f"{res.e_arranque_j:.3f} J")
        self._metric_labels["muestreo"].setText(
            f"{res.fs_real_hz:.0f} Hz (Δt={res.dt_med_us:.0f} µs)"
        )

        lbl_diag = self._metric_labels["diagnostico"]
        if report is not None:
            lbl_diag.setText(f"{report.estado_detectado} ({report.confianza_pct:.1f}%)")
            color_map = {
                "OK": "#15803d",
                "ADVERTENCIA": "#b45309",
                "CRITICO": "#b91c1c",
            }
            color_hex = color_map.get(report.severidad, "#0f172a")
            lbl_diag.setStyleSheet(f"color: {color_hex}; font-weight: bold;")
        else:
            lbl_diag.setText(f"Etiqueta CSV: {res.label}")
            lbl_diag.setStyleSheet("color: #1e3a5f; font-weight: bold;")

    def _init_empty_axes(self) -> None:
        for ax, title, ylabel in [
            (self.ax_v, "Voltaje vs Tiempo (Sin dataset cargado)", "Voltaje, V(t) [V]"),
            (self.ax_i, "Corriente vs Tiempo (Sin dataset cargado)", "Corriente, I(t) [mA]"),
        ]:
            ax.clear()
            ax.set_facecolor("#ffffff")
            ax.set_title(title, fontweight="bold", fontsize=10.5, color="#334155")
            ax.set_xlabel("Tiempo desde arranque, t [s]")
            ax.set_ylabel(ylabel)
            ax.set_xlim(0.0, 1.0)
            ax.set_ylim(0.0, 1.0)
            ax.grid(True, linestyle=":", alpha=0.75)
        self.canvas.draw_idle()

    def clear_plot(self) -> None:
        self._current_result = None
        self._current_report = None
        self._init_empty_axes()
        self._reset_sram_panel()

    def _replot_current(self) -> None:
        if self._current_result is not None:
            self.plot_analysis(self._current_result, self._current_report, self._current_baseline)

    def plot_analysis(
        self,
        res: TransientAnalysisResult,
        report: Optional[DiagnosticReport] = None,
        baseline: Optional[TransientAnalysisResult] = None,
    ) -> None:
        """Renderiza las curvas limpias y actualiza la tarjeta inferior TRANSITORIO SRAM."""
        self._current_result = res
        self._current_report = report
        self._current_baseline = baseline

        has_baseline_wave = (
            baseline is not None
            and len(baseline.t_uni) > 0
            and baseline.source_name != res.source_name
        )
        self.chk_overlay_baseline.setEnabled(has_baseline_wave)
        show_overlay = has_baseline_wave and self.chk_overlay_baseline.isChecked()

        self.ax_v.clear()
        self.ax_i.clear()
        self.ax_v.set_facecolor("#ffffff")
        self.ax_i.set_facecolor("#ffffff")

        # ======================================================================
        # PANEL IZQUIERDO: VOLTAJE V(t) [V]
        # ======================================================================
        self.ax_v.plot(
            res.t_raw, res.v_raw,
            color="#ff9999", lw=0.7, alpha=0.45, label="Crudo SRAM"
        )
        if show_overlay and baseline is not None:
            self.ax_v.plot(
                baseline.t_uni, baseline.v_smooth,
                color="#64748b", linestyle="--", lw=1.3, alpha=0.75,
                label=f"Base ({baseline.source_name})"
            )
        self.ax_v.plot(
            res.t_uni, res.v_smooth,
            "r-", lw=2.2, label="Voltaje Suavizado (V)"
        )
        self.ax_v.axhline(
            y=res.v_ss_v, color="darkred", linestyle=":", lw=1.3, alpha=0.85,
            label=f"Vss = {res.v_ss_v:.2f} V"
        )

        self.ax_v.set_title(
            f"Voltaje en Transitorio — {res.source_name} (Vss = {res.v_ss_v:.2f} V)",
            color="darkgreen", fontweight="bold", fontsize=10.5
        )
        self.ax_v.set_xlabel("Tiempo desde arranque, t [s]")
        self.ax_v.set_ylabel("Voltaje, V(t) [V]")

        t_max_x = float(res.t_uni[-1]) if len(res.t_uni) > 0 else 1.0
        v_max_y = max(1.0, float(np.max(res.v_raw)) * 1.15) if len(res.v_raw) > 0 else 15.0
        if show_overlay and baseline is not None and len(baseline.v_smooth) > 0:
            v_max_y = max(v_max_y, float(np.max(baseline.v_smooth)) * 1.15)

        self.ax_v.set_xlim(0.0, t_max_x)
        self.ax_v.set_ylim(0.0, v_max_y)
        self.ax_v.grid(True, linestyle=":", alpha=0.75)
        self.ax_v.legend(loc="lower right", fontsize=8.5)

        # ======================================================================
        # PANEL DERECHO: CORRIENTE I(t) [mA] (SIN RECUADRO SUPERPUESTO)
        # ======================================================================
        self.ax_i.plot(
            res.t_raw, res.i_raw_ma,
            color="#99bbff", lw=0.7, alpha=0.45, label="Crudo SRAM"
        )
        if show_overlay and baseline is not None:
            self.ax_i.plot(
                baseline.t_uni, baseline.i_smooth_ma,
                color="#64748b", linestyle="--", lw=1.3, alpha=0.75,
                label=f"Base ({baseline.source_name})"
            )
        self.ax_i.plot(
            res.t_uni, res.i_smooth_ma,
            "b-", lw=2.2, label="Corriente Suavizada (mA)"
        )

        t_pico_s = res.t_pico_ms / 1000.0
        self.ax_i.plot(
            t_pico_s, res.i_pico_suave_ma,
            "ro", markersize=6, zorder=5, label=f"Pico: {res.i_pico_suave_ma:.1f} mA"
        )
        self.ax_i.plot(
            res.t_tau_s, res.i_tau_obj_ma,
            "go", markersize=6, zorder=5, label=f"63.2% (τ = {res.tau_ms:.1f} ms)"
        )
        self.ax_i.axvline(x=res.t_tau_s, color="g", linestyle="--", lw=1.2, alpha=0.8)

        if res.t_estab_ms is not None:
            t_est_s = res.t_estab_ms / 1000.0
            self.ax_i.axvline(
                x=t_est_s, color="#d97706", linestyle="-.", lw=1.2, alpha=0.85,
                label=f"Estable: {res.t_estab_ms:.0f} ms"
            )

        self.ax_i.axhline(
            y=res.i_ss_ma, color="purple", linestyle=":", lw=1.3, alpha=0.85,
            label=f"Iss = {res.i_ss_ma:.1f} mA"
        )

        self.ax_i.set_title(
            f"Transitorio Detenido en {res.t_corte_ms:.0f} ms (τ = {res.tau_ms:.2f} ms)",
            color="darkgreen", fontweight="bold", fontsize=10.5
        )
        self.ax_i.set_xlabel("Tiempo desde arranque, t [s]")
        self.ax_i.set_ylabel("Corriente, I(t) [mA]")

        i_max_y = max(100.0, float(np.max(res.i_raw_ma)) * 1.10) if len(res.i_raw_ma) > 0 else 1000.0
        if show_overlay and baseline is not None and len(baseline.i_smooth_ma) > 0:
            i_max_y = max(i_max_y, float(np.max(baseline.i_smooth_ma)) * 1.10)

        self.ax_i.set_xlim(0.0, t_max_x)
        self.ax_i.set_ylim(0.0, i_max_y)
        self.ax_i.grid(True, linestyle=":", alpha=0.75)
        self.ax_i.legend(loc="upper right", fontsize=8.5)

        # Actualizar el cuadro inferior fuera de la gráfica y refrescar lienzo
        self._update_sram_panel(res, report)
        self.canvas.draw_idle()

    def export_png(self) -> Optional[Path]:
        """Guarda la figura actual limpia como archivo PNG de 300 DPI."""
        default_name = (
            f"grafica_{Path(self._current_result.source_name).stem}.png"
            if self._current_result is not None
            else "grafica_transitorio.png"
        )
        filepath, _ = QFileDialog.getSaveFileName(
            self,
            "Exportar Gráfica Transitoria",
            default_name,
            "Imagen PNG (*.png);;PDF Vectorial (*.pdf)",
        )
        if not filepath:
            return None
        out = Path(filepath)
        self.fig.savefig(out, dpi=300, bbox_inches="tight")
        return out