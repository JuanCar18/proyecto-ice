"""
Módulo: src/ui/motor_spec_panel.py
Proyecto: Diagnóstico Inteligente de Motores DC - Expo ICE Impulsa 2026
Descripción:
    Panel de captura opcional de parámetros nominales del motor (rango de voltaje,
    Rw, Inom, Istall, L) y sincronización con el catálogo SQLite / MySQL.
"""

from pathlib import Path
import sys
from typing import List, Optional

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

RAIZ_PROYECTO = Path(__file__).resolve().parents[2]

try:
    from src.core.motor_profile import MotorSpecProfile
    from src.io_bridge.mysql_motor_repo import MotorRepository
except ImportError:
    sys.path.append(str(RAIZ_PROYECTO))
    from src.core.motor_profile import MotorSpecProfile
    from src.io_bridge.mysql_motor_repo import MotorRepository


def _parse_optional_float(widget: QLineEdit) -> Optional[float]:
    txt = widget.text().strip().replace(",", ".")
    if not txt:
        return None
    try:
        val = float(txt)
        return val if val > 0 else None
    except ValueError:
        return None


class MotorSpecPanel(QWidget):
    """Widget acoplable para definir especificaciones del motor o cargarlas desde el catálogo BD."""

    profile_changed = pyqtSignal(object)  # Emite MotorSpecProfile

    def __init__(self, repo: Optional[MotorRepository] = None, parent=None):
        super().__init__(parent)
        self.repo = repo or MotorRepository()
        self.motores_catalogo: List[MotorSpecProfile] = []
        self._build_ui()
        self.sincronizar_catalogo(silencioso=True)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        grp = QGroupBox("Especificaciones del Motor (Catálogo BD / Manual)")
        grp_layout = QVBoxLayout(grp)
        grp_layout.setSpacing(6)

        # Fila de selección desde catálogo SQLite / MySQL
        db_row = QHBoxLayout()
        self.combo_catalogo = QComboBox()
        self.combo_catalogo.addItem("-- Entrada manual / Sin especificar --")
        self.combo_catalogo.currentIndexChanged.connect(self._on_catalogo_selected)
        db_row.addWidget(self.combo_catalogo, stretch=1)

        btn_sync = QPushButton("Recargar BD")
        btn_sync.setToolTip("Consulta motores reales registrados en la base de datos (SQLite / MySQL)")
        btn_sync.clicked.connect(lambda: self.sincronizar_catalogo(silencioso=False))
        db_row.addWidget(btn_sync)
        grp_layout.addLayout(db_row)

        self.lbl_db_status = QLabel("Catálogo BD: inicializando...")
        self.lbl_db_status.setStyleSheet("font-size: 8pt; color: #475569;")
        self.lbl_db_status.setWordWrap(True)
        grp_layout.addWidget(self.lbl_db_status)

        # Formulario de parámetros opcionales
        form = QFormLayout()
        form.setSpacing(4)

        self.txt_modelo = QLineEdit()
        self.txt_modelo.setPlaceholderText("Ej. RS-550 / Motor Prueba (opcional)")

        v_row = QHBoxLayout()
        self.txt_vmin = QLineEdit()
        self.txt_vmin.setPlaceholderText("V mín (ej. 6)")
        self.txt_vmax = QLineEdit()
        self.txt_vmax.setPlaceholderText("V máx (ej. 15)")
        self.txt_vnom = QLineEdit()
        self.txt_vnom.setPlaceholderText("V nom (ej. 12)")
        v_row.addWidget(self.txt_vmin)
        v_row.addWidget(self.txt_vmax)
        v_row.addWidget(self.txt_vnom)

        elec_row = QHBoxLayout()
        self.txt_rw = QLineEdit()
        self.txt_rw.setPlaceholderText("Rw Ω (opcional)")
        self.txt_l = QLineEdit()
        self.txt_l.setPlaceholderText("L mH (opcional)")
        elec_row.addWidget(self.txt_rw)
        elec_row.addWidget(self.txt_l)

        curr_row = QHBoxLayout()
        self.txt_inom = QLineEdit()
        self.txt_inom.setPlaceholderText("I nom A (opcional)")
        self.txt_istall = QLineEdit()
        self.txt_istall.setPlaceholderText("I stall A (opcional)")
        curr_row.addWidget(self.txt_inom)
        curr_row.addWidget(self.txt_istall)

        form.addRow("Modelo:", self.txt_modelo)
        form.addRow("Voltaje (V):", v_row)
        form.addRow("Devanado:", elec_row)
        form.addRow("Corriente:", curr_row)
        grp_layout.addLayout(form)

        # Botones de acción
        actions_row = QHBoxLayout()
        btn_aplicar = QPushButton("Aplicar al Diagnóstico")
        btn_aplicar.setObjectName("PrimaryBtn")
        btn_aplicar.clicked.connect(self.emitir_perfil_actual)

        btn_guardar_bd = QPushButton("Guardar en BD")
        btn_guardar_bd.setToolTip("Registra o actualiza este motor en el catálogo de base de datos")
        btn_guardar_bd.clicked.connect(self.guardar_en_catalogo)

        btn_limpiar = QPushButton("Limpiar")
        btn_limpiar.clicked.connect(self.limpiar_campos)

        actions_row.addWidget(btn_aplicar)
        actions_row.addWidget(btn_guardar_bd)
        actions_row.addWidget(btn_limpiar)
        grp_layout.addLayout(actions_row)

        layout.addWidget(grp)

    def obtener_perfil(self) -> MotorSpecProfile:
        idx = self.combo_catalogo.currentIndex()
        id_motor_sel = (
            self.motores_catalogo[idx - 1].id_motor
            if 0 < idx <= len(self.motores_catalogo)
            else None
        )
        modelo_txt = self.txt_modelo.text().strip() or "No especificado"
        return MotorSpecProfile(
            id_motor=id_motor_sel,
            modelo=modelo_txt,
            v_nom_min=_parse_optional_float(self.txt_vmin),
            v_nom_max=_parse_optional_float(self.txt_vmax),
            v_nom=_parse_optional_float(self.txt_vnom),
            rw_ohm=_parse_optional_float(self.txt_rw),
            l_mh=_parse_optional_float(self.txt_l),
            i_nominal_a=_parse_optional_float(self.txt_inom),
            i_stall_a=_parse_optional_float(self.txt_istall),
            fuente_datos="CATALOGO_BD" if id_motor_sel is not None else "MANUAL_USUARIO",
        )

    def emitir_perfil_actual(self) -> None:
        perfil = self.obtener_perfil()
        self.profile_changed.emit(perfil)

    def limpiar_campos(self) -> None:
        self.combo_catalogo.blockSignals(True)
        self.combo_catalogo.setCurrentIndex(0)
        self.combo_catalogo.blockSignals(False)
        for field in (
            self.txt_modelo,
            self.txt_vmin,
            self.txt_vmax,
            self.txt_vnom,
            self.txt_rw,
            self.txt_l,
            self.txt_inom,
            self.txt_istall,
        ):
            field.clear()
        self.emitir_perfil_actual()

    def sincronizar_catalogo(self, silencioso: bool = False) -> None:
        ok, msg = self.repo.probar_conexion()
        self.lbl_db_status.setText(msg)
        if not ok:
            if not silencioso:
                QMessageBox.warning(self, "Conexión Base de Datos", msg)
            return

        current_id = None
        curr_idx = self.combo_catalogo.currentIndex()
        if 0 < curr_idx <= len(self.motores_catalogo):
            current_id = self.motores_catalogo[curr_idx - 1].id_motor

        self.motores_catalogo = self.repo.listar_motores()
        self.combo_catalogo.blockSignals(True)
        self.combo_catalogo.clear()
        self.combo_catalogo.addItem("-- Entrada manual / Sin especificar --")
        restore_idx = 0
        for idx, m in enumerate(self.motores_catalogo, start=1):
            v_ref = m.voltaje_referencia()
            v_str = f"{v_ref:g} V" if v_ref is not None else "S/V"
            etiqueta = f"{m.fabricante} {m.modelo}".strip()
            self.combo_catalogo.addItem(f"{etiqueta} ({v_str})")
            if current_id is not None and m.id_motor == current_id:
                restore_idx = idx
        self.combo_catalogo.setCurrentIndex(restore_idx)
        self.combo_catalogo.blockSignals(False)

    def _on_catalogo_selected(self, index: int) -> None:
        if index <= 0 or index > len(self.motores_catalogo):
            return
        m = self.motores_catalogo[index - 1]
        self.txt_modelo.setText(f"{m.fabricante} {m.modelo}".strip())
        self.txt_vmin.setText(f"{m.v_nom_min:g}" if m.v_nom_min is not None else "")
        self.txt_vmax.setText(f"{m.v_nom_max:g}" if m.v_nom_max is not None else "")
        self.txt_vnom.setText(f"{m.v_nom:g}" if m.v_nom is not None else "")
        self.txt_rw.setText(f"{m.rw_ohm:g}" if m.rw_ohm is not None else "")
        self.txt_l.setText(f"{m.l_mh:g}" if m.l_mh is not None else "")
        self.txt_inom.setText(f"{m.i_nominal_a:g}" if m.i_nominal_a is not None else "")
        self.txt_istall.setText(f"{m.i_stall_a:g}" if m.i_stall_a is not None else "")
        self.profile_changed.emit(m)

    def guardar_en_catalogo(self) -> None:
        perfil = self.obtener_perfil()
        if perfil.modelo == "No especificado":
            QMessageBox.warning(
                self,
                "Modelo requerido",
                "Ingresa al menos el nombre o modelo del motor antes de guardarlo en la base de datos.",
            )
            return
        try:
            nuevo_id = self.repo.insertar_motor(perfil)
            self.sincronizar_catalogo(silencioso=True)
            for idx, m in enumerate(self.motores_catalogo, start=1):
                if m.id_motor == nuevo_id:
                    self.combo_catalogo.setCurrentIndex(idx)
                    break
            QMessageBox.information(
                self,
                "Motor Registrado en BD",
                f"Motor '{perfil.modelo}' guardado correctamente en el catálogo (ID #{nuevo_id}).",
            )
        except Exception as exc:
            QMessageBox.warning(self, "No se pudo guardar en BD", str(exc))

    # Alias de retrocompatibilidad con nombres anteriores
    def sincronizar_mysql(self) -> None:
        self.sincronizar_catalogo(silencioso=False)

    def guardar_en_mysql(self) -> None:
        self.guardar_en_catalogo()