import os
import tempfile
import traceback
from pathlib import Path

import numpy as np
from PyQt5.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QFont, QDesktopServices
from PyQt5.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QPushButton, QLabel, QProgressBar, QTextEdit
)
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg

from src.io_bridge.esp32_udp_worker import ESP32UdpWorker


class ESP32ControlDock(QDockWidget):
    """Panel acoplable con entrada ESP32, trazado del transitorio con parada automática y monitoreo."""
    diagnostico_completado = pyqtSignal(Path)

    def __init__(self, main_window, carpeta_capturas="capturas_esp32", parent=None):
        super().__init__("Control ESP32 & Transitorio", parent or main_window)
        self.main_window = main_window
        self.carpeta_capturas = os.path.abspath(carpeta_capturas)
        self.setObjectName("DockControlESP32")
        self.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea | Qt.BottomDockWidgetArea)
        self.setMinimumWidth(290)
        self.setMaximumWidth(360)

        self.worker = ESP32UdpWorker(carpeta_capturas=self.carpeta_capturas, parent=self)
        self.visualizador_transitorio_activo = False

        self._canvas = None
        self._ax_v = None
        self._ax_i = None
        self._rt_line_v = None
        self._rt_line_i = None

        self._sweep_datos = None
        self._sweep_idx = 0
        self._sweep_paso = 40

        self.timer_rt = QTimer(self)
        self.timer_rt.setInterval(20)  # 50 FPS únicamente durante el trazado del transitorio
        self.timer_rt.timeout.connect(self._ciclo_trazado_transitorio)

        self._construir_ui()
        self._conectar_senales()

    def _construir_ui(self):
        contenedor = QWidget()
        layout = QVBoxLayout(contenedor)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        # 1. Botón de Entrada ESP32 (Activar / Desactivar)
        self.btn_entrada_esp = QPushButton("ENTRADA ESP32 (UDP): DESACTIVADA")
        self.btn_entrada_esp.setCheckable(True)
        self.btn_entrada_esp.setChecked(False)
        self.btn_entrada_esp.setStyleSheet(
            "QPushButton { background-color: #475569; color: white; font-weight: bold; padding: 9px; border-radius: 4px; }"
            "QPushButton:checked { background-color: #0284c7; color: white; }"
        )
        layout.addWidget(self.btn_entrada_esp)

        # 2. Botón de Visualización del Transitorio (Auto-Stop al finalizar la prueba)
        self.btn_grafica_rt = QPushButton("VISUALIZAR TRANSITORIO: DESACTIVADA")
        self.btn_grafica_rt.setCheckable(True)
        self.btn_grafica_rt.setChecked(False)
        self.btn_grafica_rt.setStyleSheet(
            "QPushButton { background-color: #475569; color: white; font-weight: bold; padding: 9px; border-radius: 4px; }"
            "QPushButton:checked { background-color: #16a34a; color: white; }"
        )
        layout.addWidget(self.btn_grafica_rt)

        # 3. Sección de Monitoreo en Vivo
        grp_mon = QGroupBox("Monitoreo en Vivo")
        lay_mon = QVBoxLayout(grp_mon)
        lay_mon.setSpacing(8)

        self.lbl_estado = QLabel("Estado: Entrada ESP32 apagada")
        self.lbl_estado.setWordWrap(True)
        self.lbl_estado.setStyleSheet("font-weight: bold; color: #64748b;")
        lay_mon.addWidget(self.lbl_estado)

        fila_vi = QHBoxLayout()
        self.lbl_v = QLabel("V: 0.00 V")
        self.lbl_v.setAlignment(Qt.AlignCenter)
        self.lbl_v.setFont(QFont("Consolas", 11, QFont.Bold))
        self.lbl_v.setStyleSheet(
            "background-color: #fef2f2; color: #dc2626; border: 1px solid #fca5a5; border-radius: 4px; padding: 6px;"
        )

        self.lbl_i = QLabel("I: 0.0 mA")
        self.lbl_i.setAlignment(Qt.AlignCenter)
        self.lbl_i.setFont(QFont("Consolas", 11, QFont.Bold))
        self.lbl_i.setStyleSheet(
            "background-color: #eff6ff; color: #2563eb; border: 1px solid #93c5fd; border-radius: 4px; padding: 6px;"
        )

        self.lbl_hz = QLabel("OFF")
        self.lbl_hz.setAlignment(Qt.AlignCenter)
        self.lbl_hz.setFont(QFont("Consolas", 10, QFont.Bold))
        self.lbl_hz.setStyleSheet(
            "background-color: #f0fdf4; color: #16a34a; border: 1px solid #86efac; border-radius: 4px; padding: 6px;"
        )

        fila_vi.addWidget(self.lbl_v, 2)
        fila_vi.addWidget(self.lbl_i, 2)
        fila_vi.addWidget(self.lbl_hz, 1)
        lay_mon.addLayout(fila_vi)

        self.barra_sram = QProgressBar()
        self.barra_sram.setRange(0, 15000)
        self.barra_sram.setValue(0)
        self.barra_sram.setFormat("SRAM: %v / %m muestras")
        lay_mon.addWidget(self.barra_sram)

        self.lbl_ultimo_diag = QLabel("Último autodiagnóstico: Sin capturas aún")
        self.lbl_ultimo_diag.setWordWrap(True)
        self.lbl_ultimo_diag.setStyleSheet(
            "background-color: #f8fafc; color: #1e293b; border: 1px solid #cbd5e1; "
            "border-radius: 4px; padding: 6px; font-size: 11px;"
        )
        lay_mon.addWidget(self.lbl_ultimo_diag)

        self.btn_abrir_carpeta = QPushButton("Abrir Carpeta capturas_esp32/")
        self.btn_abrir_carpeta.setStyleSheet(
            "background-color: #334155; color: white; font-weight: bold; padding: 6px; border-radius: 4px;"
        )
        lay_mon.addWidget(self.btn_abrir_carpeta)

        layout.addWidget(grp_mon)
        layout.addStretch()
        self.setWidget(contenedor)

    def _conectar_senales(self):
        self.btn_entrada_esp.toggled.connect(self._on_toggle_entrada)
        self.btn_grafica_rt.toggled.connect(self._on_toggle_grafica_rt)
        self.btn_abrir_carpeta.clicked.connect(self._abrir_carpeta_capturas)

        self.worker.telemetria_recibida.connect(self._on_telemetria)
        self.worker.progreso_sram.connect(self._on_progreso_sram)
        self.worker.captura_cruda_lista.connect(self._on_captura_cruda_lista)
        self.worker.log_mensaje.connect(self._escribir_consola)
        self.worker.estado_entrada_cambiado.connect(self._sincronizar_boton_entrada)

    def _abrir_carpeta_capturas(self):
        os.makedirs(self.carpeta_capturas, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.carpeta_capturas))

    def _escribir_consola(self, texto: str):
        try:
            if hasattr(self.main_window, "_log_console") and callable(self.main_window._log_console):
                self.main_window._log_console(texto)
                return
            consolas = self.main_window.findChildren(QTextEdit)
            if consolas:
                consolas[-1].append(texto)
            else:
                print(texto)
        except Exception as e:
            print(f"[FALLO CONSOLA] {texto} ({e})")

    def _sincronizar_boton_entrada(self, activa: bool):
        self.btn_entrada_esp.blockSignals(True)
        self.btn_entrada_esp.setChecked(activa)
        self.btn_entrada_esp.setText(
            "ENTRADA ESP32 (UDP): ACTIVADA" if activa else "ENTRADA ESP32 (UDP): DESACTIVADA"
        )
        self.btn_entrada_esp.blockSignals(False)

    def _detener_visualizacion_transitorio(self, motivo: str = ""):
        self.timer_rt.stop()
        self._sweep_datos = None
        self.visualizador_transitorio_activo = False

        self.btn_grafica_rt.blockSignals(True)
        self.btn_grafica_rt.setChecked(False)
        self.btn_grafica_rt.setText("VISUALIZAR TRANSITORIO: DESACTIVADA")
        self.btn_grafica_rt.blockSignals(False)

        if motivo:
            self._escribir_consola(motivo)

    def _localizar_lienzo_central(self) -> bool:
        if self._canvas is not None and len(self._canvas.figure.axes) >= 2:
            return True
        for canvas in self.main_window.findChildren(FigureCanvasQTAgg):
            if len(canvas.figure.axes) >= 2:
                self._canvas = canvas
                self._ax_v = canvas.figure.axes[0]
                self._ax_i = canvas.figure.axes[1]
                return True
        return False

    def _preparar_lienzo_transitorio(self):
        if not self._localizar_lienzo_central():
            self._escribir_consola("[ESP32 ANOMALÍA] No se localizó el lienzo gráfico central.")
            return

        self._ax_v.clear()
        self._ax_i.clear()

        self._rt_line_v, = self._ax_v.plot([], [], color="#dc2626", lw=2.0, label="Voltaje Transitorio V(t)")
        self._rt_line_i, = self._ax_i.plot([], [], color="#2563eb", lw=2.0, label="Corriente Transitoria I(t)")

        self._ax_v.set_title("Voltaje — Respuesta Transitoria", color="#15803d", fontweight="bold")
        self._ax_v.set_xlabel("Tiempo, t [s]")
        self._ax_v.set_ylabel("Voltaje, V(t) [V]")
        self._ax_v.grid(True, linestyle=":", alpha=0.75)
        self._ax_v.legend(loc="lower right")

        self._ax_i.set_title("Corriente — Respuesta Transitoria", color="#15803d", fontweight="bold")
        self._ax_i.set_xlabel("Tiempo, t [s]")
        self._ax_i.set_ylabel("Corriente, I(t) [mA]")
        self._ax_i.grid(True, linestyle=":", alpha=0.75)
        self._ax_i.legend(loc="upper right")

        self._canvas.draw_idle()

    def _on_toggle_entrada(self, activo: bool):
        if activo:
            self.barra_sram.setValue(0)
            self.worker.activar_entrada()
            self.lbl_estado.setText("Estado: Escuchando UDP 5000...")
            self.lbl_estado.setStyleSheet("font-weight: bold; color: #0284c7;")
        else:
            self._detener_visualizacion_transitorio()
            self.worker.desactivar_entrada()
            self.lbl_estado.setText("Estado: Entrada ESP32 apagada")
            self.lbl_estado.setStyleSheet("font-weight: bold; color: #64748b;")
            self.lbl_hz.setText("OFF")

    def _on_toggle_grafica_rt(self, activo: bool):
        self.visualizador_transitorio_activo = activo
        if activo:
            self.btn_grafica_rt.setText("VISUALIZAR TRANSITORIO: EN ESPERA")
            self._escribir_consola(
                "[UI] Visualización de transitorio ACTIVADA (se detendrá automáticamente al finalizar la prueba)."
            )
        else:
            self._detener_visualizacion_transitorio("[UI] Visualización de transitorio DESACTIVADA.")

    def _on_telemetria(self, estado: str, v: float, i: float, ip: str, hz: float):
        self.lbl_estado.setText(f"ESP32 ({ip}) — {estado}")
        if "ERROR" in estado:
            self.lbl_estado.setStyleSheet("font-weight: bold; color: #dc2626;")
        elif "FINALIZADA" in estado:
            self.lbl_estado.setStyleSheet("font-weight: bold; color: #d97706;")
        else:
            self.lbl_estado.setStyleSheet("font-weight: bold; color: #16a34a;")

        self.lbl_v.setText(f"V: {v:.2f} V")
        self.lbl_i.setText(f"I: {i:.1f} mA")
        self.lbl_hz.setText(f"{hz:.0f} Hz")

    def _on_progreso_sram(self, recibidas: int, total: int):
        self.barra_sram.setMaximum(max(recibidas, total))
        self.barra_sram.setValue(recibidas)

    def _diagnosticar_guardar_y_cargar(self, datos_proc: dict):
        tmp_path = Path(tempfile.gettempdir()) / "_ice2026_temp_eval.csv"
        estado_detectado = "SIN_CLASIFICAR"
        confianza_pct = 0.0

        try:
            self.worker.escribir_csv_en_ruta(tmp_path, datos_proc, "EVALUANDO")

            if hasattr(self.main_window, "processor") and hasattr(self.main_window, "ai_engine"):
                res_temp = self.main_window.processor.analyze_csv(tmp_path)
                reporte = self.main_window.ai_engine.diagnose(res_temp)
                estado_detectado = str(reporte.estado_detectado).strip().upper()
                confianza_pct = float(getattr(reporte, "confianza_pct", 0.0))

                # Si hay un motor seleccionado en BD, refinar la carpeta destino con la física de placa
                asistente = getattr(self.main_window, "_catalog_assistant", None)
                if asistente is not None:
                    info_bd = asistente.evaluar_asistencia_fisica(res_temp, reporte)
                    estado_detectado = info_bd.get("estado_final", estado_detectado)

                self._escribir_consola(
                    f"[AUTODIAGNÓSTICO IA] Estado detectado: {estado_detectado} ({confianza_pct:.1f}%)"
                )
        except Exception as exc:
            self._escribir_consola(f"[ESP32 ANOMALÍA DIAGNÓSTICO] {exc}\n{traceback.format_exc()}")
        finally:
            if tmp_path.exists():
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

        try:
            ruta_final = self.worker.guardar_csv_por_diagnostico(datos_proc, estado_detectado)
            self.lbl_ultimo_diag.setText(
                f"Último autodiagnóstico:\n"
                f"• Estado: {estado_detectado} ({confianza_pct:.1f}%)\n"
                f"• Archivo: {ruta_final.parent.name}/{ruta_final.name}"
            )

            if hasattr(self.main_window, "load_csv_files") and callable(self.main_window.load_csv_files):
                self.main_window.load_csv_files([ruta_final])

            self.diagnostico_completado.emit(ruta_final)
            self._escribir_consola(
                "[PRUEBA FINALIZADA] Archivo único guardado. Apaga el motor (>2 s en 0 mA) para habilitar la siguiente prueba."
            )
        except Exception as exc:
            self._escribir_consola(f"[ESP32 ANOMALÍA GUARDADO/CARGA] {exc}\n{traceback.format_exc()}")

    def _on_captura_cruda_lista(self, datos_proc: dict):
        self.barra_sram.setValue(self.barra_sram.maximum())

        if not datos_proc:
            if self.visualizador_transitorio_activo:
                self._detener_visualizacion_transitorio(
                    "[UI] Prueba descartada por ruido; visualización detenida."
                )
            return

        if self.visualizador_transitorio_activo and self._localizar_lienzo_central():
            self._preparar_lienzo_transitorio()
            t_plot = datos_proc["t_plot"]
            v_plot = datos_proc["v_plot"]
            i_plot = datos_proc["i_plot"]
            if len(t_plot) > 2:
                self._ax_v.set_xlim(0.0, float(t_plot[-1]))
                self._ax_i.set_xlim(0.0, float(t_plot[-1]))
                self._ax_v.set_ylim(0.0, max(1.0, float(np.max(v_plot)) * 1.15))
                self._ax_i.set_ylim(0.0, max(100.0, float(np.max(i_plot)) * 1.12))
                self._sweep_datos = datos_proc
                self._sweep_paso = max(1, len(t_plot) // 45)
                self._sweep_idx = self._sweep_paso
                self.btn_grafica_rt.setText("VISUALIZAR TRANSITORIO: GRAFICANDO...")
                if not self.timer_rt.isActive():
                    self.timer_rt.start()
                return

        self._diagnosticar_guardar_y_cargar(datos_proc)

    def _ciclo_trazado_transitorio(self):
        if not self.visualizador_transitorio_activo or self._sweep_datos is None:
            self.timer_rt.stop()
            return

        t_p = self._sweep_datos["t_plot"]
        v_p = self._sweep_datos["v_plot"]
        i_p = self._sweep_datos["i_plot"]
        n = min(self._sweep_idx, len(t_p))

        self._rt_line_v.set_data(t_p[:n], v_p[:n])
        self._rt_line_i.set_data(t_p[:n], i_p[:n])
        self._canvas.draw_idle()

        if n >= len(t_p):
            datos_listos = self._sweep_datos
            self._detener_visualizacion_transitorio(
                "[UI] Transitorio completado. Visualización detenida automáticamente."
            )
            self._diagnosticar_guardar_y_cargar(datos_listos)
        else:
            self._sweep_idx += self._sweep_paso

    def closeEvent(self, event):
        self.timer_rt.stop()
        self.worker.desactivar_entrada()
        super().closeEvent(event)