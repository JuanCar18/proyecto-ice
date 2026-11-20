import os
from datetime import datetime
from typing import Any, Dict, List

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont, QColor
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QPushButton, QProgressBar, QTableWidget, QTableWidgetItem,
    QHeaderView, QTextEdit, QFileDialog, QMessageBox, QComboBox
)

from src.core.motor_catalog_helper import extraer_metricas_res


class VentanaDiagnosticoIA(QWidget):
    """Ventana independiente para visualizar el diagnóstico inteligente y la comparativa con BD."""
    solicitar_anterior = pyqtSignal()
    solicitar_siguiente = pyqtSignal()
    motor_cambiado_en_ventana = pyqtSignal(int)

    ESTILOS_ESTADO = {
        "NORMAL": ("#dcfce7", "#166534", "#22c55e", "OPERACIÓN NOMINAL"),
        "SOBRECARGA": ("#fef3c7", "#92400e", "#f59e0b", "ADVERTENCIA — CARGA EXCESIVA"),
        "BAJO_VOLTAJE": ("#fef3c7", "#92400e", "#f59e0b", "ADVERTENCIA — SUBTENSIÓN"),
        "ROZAMIENTO_MECANICO": ("#ffedd5", "#9a3412", "#f97316", "ADVERTENCIA — FRICCIÓN MECÁNICA"),
        "BLOQUEO_PARCIAL": ("#fee2e2", "#991b1b", "#ef4444", "CRÍTICO — RIESGO TÉRMICO ROTOR"),
        "NO_ESTABILIZADO": ("#fae8ff", "#86198f", "#d946ef", "PROTECCIÓN SRAM — MOTOR NO ESTABILIZADO"),
    }

    RECOMENDACIONES = {
        "NORMAL": "• Operación dentro de parámetros nominales.\n• Continuar con el programa de monitoreo preventivo periódico.",
        "SOBRECARGA": "• Reducir el par o masa acoplada al eje de salida.\n• Verificar relación de transmisión y temperatura del devanado de armadura.",
        "BAJO_VOLTAJE": "• Revisar tensión de salida de la fuente DC y caídas en el cableado/etapa de potencia.\n• Verificar estado de carga de baterías o regulador.",
        "ROZAMIENTO_MECANICO": "• Inspeccionar lubricación y desgaste en bujes o rodamientos.\n• Verificar alineación mecánica del acoplamiento del eje.",
        "BLOQUEO_PARCIAL": "• ¡DETENER OPERACIÓN! Eliminar obstrucción mecánica en rotor o carga.\n• Medir resistencia de aislamiento y continuidad de escobillas antes de reenergizar.",
        "NO_ESTABILIZADO": (
            "• PROTECCIÓN SRAM ACTIVADA: El motor no alcanzó régimen estable antes de llenar la RAM del ESP32; "
            "el búfer fue descargado y liberado automáticamente.\n"
            "• Reducir carga mecánica o verificar obstrucciones en el eje."
        ),
    }

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("Diagnóstico Inteligente de Motor DC — Resultado Detallado")
        self.resize(740, 690)
        self._ultimo_texto_reporte = ""
        self._ultimo_nombre_archivo = "diagnostico"

        self._construir_ui()

    def _construir_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        # 1. Encabezado de archivo y selector directo de Motor BD SQLite
        fila_top = QHBoxLayout()
        self.lbl_archivo = QLabel("Ensayo: Sin ensayo seleccionado")
        self.lbl_archivo.setFont(QFont("Segoe UI", 10, QFont.Bold))
        self.lbl_archivo.setStyleSheet("color: #1e293b;")

        lbl_tag_bd = QLabel("Motor BD:")
        lbl_tag_bd.setFont(QFont("Segoe UI", 9, QFont.Bold))
        lbl_tag_bd.setStyleSheet("color: #0369a1;")

        self.cmb_motor_bd = QComboBox()
        self.cmb_motor_bd.setMinimumWidth(260)
        self.cmb_motor_bd.addItem("-- Sin especificar (Modo genérico IA) --")
        self.cmb_motor_bd.setStyleSheet(
            "QComboBox { background-color: #e0f2fe; color: #0369a1; font-weight: bold; "
            "padding: 4px 8px; border: 1px solid #7dd3fc; border-radius: 4px; }"
        )
        self.cmb_motor_bd.currentIndexChanged.connect(self.motor_cambiado_en_ventana.emit)

        fila_top.addWidget(self.lbl_archivo, 1)
        fila_top.addWidget(lbl_tag_bd)
        fila_top.addWidget(self.cmb_motor_bd)
        layout.addLayout(fila_top)

        # 2. Tarjeta principal de estado diagnosticado
        self.card_estado = QGroupBox("Resultado del Diagnóstico Inteligente")
        lay_card = QVBoxLayout(self.card_estado)

        self.lbl_estado_titulo = QLabel("ESTADO: EN ESPERA DE DATOS")
        self.lbl_estado_titulo.setFont(QFont("Segoe UI", 15, QFont.Bold))
        self.lbl_estado_titulo.setAlignment(Qt.AlignCenter)
        self.lbl_estado_titulo.setStyleSheet(
            "background-color: #f1f5f9; color: #334155; border: 2px solid #94a3b8; "
            "border-radius: 6px; padding: 11px;"
        )
        lay_card.addWidget(self.lbl_estado_titulo)

        self.lbl_subestado = QLabel("Confianza IA: -- %  |  Severidad: --")
        self.lbl_subestado.setAlignment(Qt.AlignCenter)
        self.lbl_subestado.setFont(QFont("Segoe UI", 10, QFont.Bold))
        lay_card.addWidget(self.lbl_subestado)
        layout.addWidget(self.card_estado)

        # 3. Probabilidades por escenario (Random Forest)
        self.grp_probs = QGroupBox("Distribución de Probabilidad por Escenario (IA)")
        lay_probs = QVBoxLayout(self.grp_probs)
        lay_probs.setSpacing(5)

        self.barras_prob: Dict[str, QProgressBar] = {}
        for clase in ("NORMAL", "SOBRECARGA", "BAJO_VOLTAJE", "ROZAMIENTO_MECANICO", "BLOQUEO_PARCIAL"):
            fila = QHBoxLayout()
            lbl = QLabel(clase)
            lbl.setFixedWidth(175)
            lbl.setFont(QFont("Consolas", 9, QFont.Bold))

            barra = QProgressBar()
            barra.setRange(0, 100)
            barra.setValue(0)
            barra.setFormat("%v%")
            barra.setFixedHeight(18)

            fila.addWidget(lbl)
            fila.addWidget(barra, 1)
            lay_probs.addLayout(fila)
            self.barras_prob[clase] = barra

        layout.addWidget(self.grp_probs)

        # 4. Tabla comparativa con parámetros nominales de Base de Datos SQLite
        self.grp_bd = QGroupBox("Verificación Física contra Catálogo de Motores (SQLite)")
        lay_bd = QVBoxLayout(self.grp_bd)
        lay_bd.setSpacing(6)

        self.tabla_comparativa = QTableWidget(0, 4)
        self.tabla_comparativa.setHorizontalHeaderLabels(["Parámetro", "Medido", "Referencia Placa BD", "Estado"])
        self.tabla_comparativa.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tabla_comparativa.verticalHeader().setVisible(False)
        self.tabla_comparativa.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tabla_comparativa.setMinimumHeight(165)
        lay_bd.addWidget(self.tabla_comparativa)

        self.txt_analisis = QTextEdit()
        self.txt_analisis.setReadOnly(True)
        self.txt_analisis.setMinimumHeight(115)
        self.txt_analisis.setStyleSheet("background-color: #f8fafc; color: #0f172a; font-size: 11px;")
        lay_bd.addWidget(self.txt_analisis)

        layout.addWidget(self.grp_bd, 1)

        # 5. Barra inferior de navegación y exportación
        fila_btns = QHBoxLayout()
        self.btn_ant = QPushButton("◀ Ensayo Anterior")
        self.btn_sig = QPushButton("Ensayo Siguiente ▶")
        self.btn_exportar = QPushButton("Guardar Reporte TXT")
        self.btn_exportar.setStyleSheet("background-color: #0284c7; color: white; font-weight: bold; padding: 6px 12px;")
        self.btn_cerrar = QPushButton("Cerrar Ventana")

        self.btn_ant.clicked.connect(self.solicitar_anterior.emit)
        self.btn_sig.clicked.connect(self.solicitar_siguiente.emit)
        self.btn_exportar.clicked.connect(self._exportar_txt)
        self.btn_cerrar.clicked.connect(self.hide)

        fila_btns.addWidget(self.btn_ant)
        fila_btns.addWidget(self.btn_sig)
        fila_btns.addStretch()
        fila_btns.addWidget(self.btn_exportar)
        fila_btns.addWidget(self.btn_cerrar)
        layout.addLayout(fila_btns)

    def poblar_motores_bd(self, motores_bd: List[Dict[str, Any]], indice_activo: int = 0):
        self.cmb_motor_bd.blockSignals(True)
        self.cmb_motor_bd.clear()
        self.cmb_motor_bd.addItem("-- Sin especificar (Modo genérico IA) --")
        for m in motores_bd:
            self.cmb_motor_bd.addItem(f"{m['modelo']} ({m['v_nom']}V / {m['i_nom_a']}A)")
        if 0 <= indice_activo < self.cmb_motor_bd.count():
            self.cmb_motor_bd.setCurrentIndex(indice_activo)
        self.cmb_motor_bd.blockSignals(False)

    def actualizar_diagnostico(self, res, report, info_bd: Dict[str, Any]):
        self.grp_probs.setVisible(True)
        self.grp_bd.setVisible(True)

        archivo = getattr(res, "source_name", None) or os.path.basename(str(getattr(res, "file_path", "ensayo.csv")))
        self._ultimo_nombre_archivo = os.path.splitext(archivo)[0]
        estado = info_bd.get("estado_final", getattr(report, "estado_detectado", "NORMAL")).upper()
        confianza = float(info_bd.get("confianza_pct", getattr(report, "confianza_pct", 0.0)))
        motor_nombre = info_bd.get("motor_nombre", "Sin especificar")
        ajuste_bd = info_bd.get("ajuste_aplicado", False)

        bg, fg, border, desc_sev = self.ESTILOS_ESTADO.get(
            estado, ("#f1f5f9", "#1e293b", "#64748b", info_bd.get("severidad", "INFO"))
        )

        self.lbl_archivo.setText(f"Ensayo: {archivo}")
        self.lbl_estado_titulo.setText(f"ESTADO: {estado}")
        self.lbl_estado_titulo.setStyleSheet(
            f"background-color: {bg}; color: {fg}; border: 2px solid {border}; "
            f"border-radius: 6px; padding: 11px; font-weight: bold;"
        )

        origen_txt = "IA + Calibración Catálogo BD" if ajuste_bd else "Modelo IA Random Forest + Física RL"
        self.lbl_subestado.setText(
            f"Confianza IA: {confianza:.1f}%   |   {desc_sev}   |   Fuente: {origen_txt}"
        )

        probs = getattr(report, "probabilidades", None) or getattr(report, "probabilities", None) or {}
        if not probs and estado in self.barras_prob:
            probs = {estado: confianza}
        for clase, barra in self.barras_prob.items():
            val = int(round(float(probs.get(clase, 0.0))))
            barra.setValue(max(0, min(100, val)))

        filas_comp = info_bd.get("comparativa", [])
        self.tabla_comparativa.setRowCount(len(filas_comp))
        for r_idx, (param, medido, ref, est_item) in enumerate(filas_comp):
            self.tabla_comparativa.setItem(r_idx, 0, QTableWidgetItem(param))
            item_med = QTableWidgetItem(medido)
            item_med.setFont(QFont("Consolas", 9, QFont.Bold))
            self.tabla_comparativa.setItem(r_idx, 1, item_med)
            self.tabla_comparativa.setItem(r_idx, 2, QTableWidgetItem(ref))

            item_est = QTableWidgetItem(est_item)
            item_est.setTextAlignment(Qt.AlignCenter)
            if est_item == "OK":
                item_est.setForeground(QColor("#15803d"))
            elif "FUERA" in est_item or "EXCESO" in est_item or "PICO" in est_item:
                item_est.setForeground(QColor("#b91c1c"))
            self.tabla_comparativa.setItem(r_idx, 3, item_est)

        metricas = info_bd.get("metricas") or extraer_metricas_res(res)
        v_ss = metricas["v_ss"]
        i_ss = metricas["i_ss"]
        i_pk = metricas["i_pico"]
        tau = metricas["tau_ms"]

        obs_txt = "\n".join(f"• {o}" for o in info_bd.get("observaciones", []))
        rec_txt = self.RECOMENDACIONES.get(estado, "• Revisar parámetros eléctricos y mecánicos del motor.")

        detalle_completo = (
            f"VALIDACIÓN CON BASE DE DATOS ({motor_nombre}):\n{obs_txt}\n\n"
            f"MÉTRICAS DEL TRANSITORIO:\n"
            f"• Vss = {v_ss:.2f} V | Iss = {i_ss:.1f} mA | Ipico = {i_pk:.1f} mA | τ = {tau:.2f} ms\n\n"
            f"ACCIÓN RECOMENDADA:\n{rec_txt}"
        )
        self.txt_analisis.setPlainText(detalle_completo)
        self._ultimo_texto_reporte = (
            f"REPORTE DE DIAGNÓSTICO INTELIGENTE — EXPO ICE IMPULSA 2026\n"
            f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Archivo: {archivo}\n"
            f"Motor de Catálogo: {motor_nombre}\n"
            f"Estado Diagnosticado: {estado} (Confianza: {confianza:.1f}%)\n"
            f"{'-' * 60}\n{detalle_completo}\n"
        )

    def _exportar_txt(self):
        if not self._ultimo_texto_reporte:
            return
        ruta, _ = QFileDialog.getSaveFileName(
            self,
            "Guardar Reporte de Diagnóstico",
            f"reporte_{self._ultimo_nombre_archivo}.txt",
            "Archivos de Texto (*.txt)",
        )
        if ruta:
            with open(ruta, "w", encoding="utf-8") as f:
                f.write(self._ultimo_texto_reporte)
            QMessageBox.information(self, "Reporte Guardado", f"Reporte exportado en:\n{ruta}")