"""
Módulo: src/core/db_manager.py
Proyecto: Diagnóstico Inteligente de Motores DC - Expo ICE Impulsa 2026
Descripción:
    Gestor portátil de base de datos SQLite3 para el catálogo de motores DC
    industriales reales y la bitácora histórica de diagnósticos.
    Se integra nativamente con MotorSpecProfile (motor_profile.py) y
    DiagnosticReport (ai_engine.py) sin requerir servidor MySQL/XAMPP.
"""

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

RAIZ_PROYECTO = Path(__file__).resolve().parents[2]

try:
    from src.core.motor_profile import MotorSpecProfile
except ImportError:
    sys.path.append(str(RAIZ_PROYECTO))
    from src.core.motor_profile import MotorSpecProfile


class DatabaseManager:
    """Administra el catálogo de motores DC y el historial de diagnósticos en SQLite."""

    def __init__(self, config_path: Optional[str | Path] = None):
        self.project_root = RAIZ_PROYECTO
        cfg_file = (
            Path(config_path)
            if config_path is not None
            else self.project_root / "database" / "db_config.json"
        )

        if cfg_file.exists():
            with open(cfg_file, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            self.db_path = self.project_root / cfg.get("db_path", "database/ice2026_motores.db")
            self.sql_schema_path = self.project_root / cfg.get("schema_path", "database/schema_motores_dc.sql")
            auto_init = bool(cfg.get("auto_init", True))
        else:
            self.db_path = self.project_root / "database" / "ice2026_motores.db"
            self.sql_schema_path = self.project_root / "database" / "schema_motores_dc.sql"
            auto_init = True

        if auto_init:
            self._inicializar_bd()

    def _conectar(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _inicializar_bd(self) -> None:
        """Crea database/ice2026_motores.db y ejecuta schema_motores_dc.sql si no existe."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.sql_schema_path.exists():
            raise FileNotFoundError(f"No se encontró el esquema SQL en: {self.sql_schema_path}")

        with open(self.sql_schema_path, "r", encoding="utf-8") as f:
            script_sql = f.read()

        with self._conectar() as conn:
            conn.executescript(script_sql)

    @staticmethod
    def _fila_a_perfil(row: sqlite3.Row | Dict[str, Any]) -> MotorSpecProfile:
        """Convierte un registro SQL de `motores_industriales` en un `MotorSpecProfile`."""
        data = dict(row)
        return MotorSpecProfile(
            id_motor=data.get("id_motor"),
            fabricante=data.get("fabricante") or "",
            modelo=data.get("modelo") or "No especificado",
            v_nom_min=data.get("v_nom_min"),
            v_nom_max=data.get("v_nom_max"),
            v_nom=data.get("v_nom"),
            rw_ohm=data.get("rw_ohm"),
            l_mh=data.get("l_mh"),
            i_vacio_a=data.get("i_vacio_a"),
            i_nominal_a=data.get("i_nominal_a"),
            i_stall_a=data.get("i_stall_a"),
            rpm_nominal=data.get("rpm_nominal"),
            fuente_datos=data.get("fuente_datasheet") or "CATALOGO_SQLITE",
        )

    # ==========================================================================
    # CONSULTAS AL CATÁLOGO DE MOTORES DC
    # ==========================================================================
    def obtener_todos_los_motores(self) -> List[Dict[str, Any]]:
        """Devuelve todos los motores como diccionarios (ideal para tablas o combos UI)."""
        query = """
            SELECT id_motor, fabricante, modelo, aplicacion_industrial,
                   v_nom_min, v_nom_max, v_nom, rw_ohm, l_mh,
                   i_vacio_a, i_nominal_a, i_stall_a, rpm_nominal, fuente_datasheet
            FROM motores_industriales
            ORDER BY v_nom ASC, fabricante ASC, modelo ASC;
        """
        with self._conectar() as conn:
            return [dict(row) for row in conn.execute(query).fetchall()]

    def listar_perfiles_motores(self) -> List[MotorSpecProfile]:
        """Devuelve el catálogo completo convertido directamente a objetos `MotorSpecProfile`."""
        query = "SELECT * FROM motores_industriales ORDER BY v_nom ASC, fabricante ASC, modelo ASC;"
        with self._conectar() as conn:
            return [self._fila_a_perfil(row) for row in conn.execute(query).fetchall()]

    def obtener_perfil_motor(self, id_motor: int) -> Optional[MotorSpecProfile]:
        """Obtiene el `MotorSpecProfile` listo para `ia.set_motor_profile(perfil)`."""
        query = "SELECT * FROM motores_industriales WHERE id_motor = ?;"
        with self._conectar() as conn:
            row = conn.execute(query, (id_motor,)).fetchone()
            return self._fila_a_perfil(row) if row else None

    def filtrar_perfiles_por_voltaje(self, voltaje_bus: float) -> List[MotorSpecProfile]:
        """Filtra motores compatibles con el riel activo en la matriz (ej. 5V, 10V, 12V, 24V)."""
        query = """
            SELECT * FROM motores_industriales
            WHERE ? BETWEEN COALESCE(v_nom_min, v_nom * 0.8) AND COALESCE(v_nom_max, v_nom * 1.1)
            ORDER BY fabricante ASC, modelo ASC;
        """
        with self._conectar() as conn:
            rows = conn.execute(query, (float(voltaje_bus),)).fetchall()
            return [self._fila_a_perfil(r) for r in rows]

    def agregar_motor(self, perfil: MotorSpecProfile, aplicacion_industrial: str = "") -> int:
        """
        Registra o actualiza un motor en `motores_industriales` preservando su `id_motor`
        para mantener intacta la integridad referencial con `historial_diagnosticos`.
        """
        fabricante = perfil.fabricante.strip() or "Genérico"
        modelo = perfil.modelo.strip() or "No especificado"
        app_ind = aplicacion_industrial.strip() or None
        v_nom = perfil.voltaje_referencia() or 12.0

        with self._conectar() as conn:
            existing_id: Optional[int] = None
            if perfil.id_motor is not None:
                row = conn.execute(
                    "SELECT id_motor FROM motores_industriales WHERE id_motor = ?;",
                    (int(perfil.id_motor),),
                ).fetchone()
                if row:
                    existing_id = int(row["id_motor"])

            if existing_id is None:
                row = conn.execute(
                    "SELECT id_motor FROM motores_industriales WHERE fabricante = ? AND modelo = ?;",
                    (fabricante, modelo),
                ).fetchone()
                if row:
                    existing_id = int(row["id_motor"])

            if existing_id is not None:
                conn.execute(
                    """
                    UPDATE motores_industriales
                    SET fabricante = ?,
                        modelo = ?,
                        aplicacion_industrial = COALESCE(?, aplicacion_industrial),
                        v_nom_min = ?,
                        v_nom_max = ?,
                        v_nom = ?,
                        rw_ohm = ?,
                        l_mh = ?,
                        i_vacio_a = ?,
                        i_nominal_a = ?,
                        i_stall_a = ?,
                        rpm_nominal = ?,
                        fuente_datasheet = ?
                    WHERE id_motor = ?;
                    """,
                    (
                        fabricante,
                        modelo,
                        app_ind,
                        perfil.v_nom_min,
                        perfil.v_nom_max,
                        v_nom,
                        perfil.rw_ohm,
                        perfil.l_mh,
                        perfil.i_vacio_a,
                        perfil.i_nominal_a,
                        perfil.i_stall_a,
                        perfil.rpm_nominal,
                        perfil.fuente_datos,
                        existing_id,
                    ),
                )
                conn.commit()
                return existing_id

            cursor = conn.execute(
                """
                INSERT INTO motores_industriales (
                    fabricante, modelo, aplicacion_industrial,
                    v_nom_min, v_nom_max, v_nom,
                    rw_ohm, l_mh, i_vacio_a, i_nominal_a, i_stall_a,
                    rpm_nominal, fuente_datasheet
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    fabricante,
                    modelo,
                    app_ind,
                    perfil.v_nom_min,
                    perfil.v_nom_max,
                    v_nom,
                    perfil.rw_ohm,
                    perfil.l_mh,
                    perfil.i_vacio_a,
                    perfil.i_nominal_a,
                    perfil.i_stall_a,
                    perfil.rpm_nominal,
                    perfil.fuente_datos,
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    # ==========================================================================
    # BITÁCORA DE DIAGNÓSTICOS (INTEGRACIÓN DIRECTA CON `DiagnosticReport`)
    # ==========================================================================
    def registrar_reporte(self, reporte: Any, id_motor: Optional[int] = None) -> int:
        """
        Guarda directamente un objeto `DiagnosticReport` generado por `MotorDiagnosticAI.diagnose()`.
        """
        params = getattr(reporte, "parametros_clave", {}) or {}
        motor_id_final = id_motor if id_motor is not None else getattr(reporte, "id_motor", None)
        return self.registrar_diagnostico(
            id_motor=motor_id_final,
            archivo_csv=reporte.archivo,
            estado_detectado=reporte.estado_detectado,
            confianza_pct=float(reporte.confianza_pct),
            severidad=reporte.severidad,
            v_ss_v=params.get("v_ss_v"),
            i_pico_a=params.get("i_pico_suave_a"),
            i_ss_a=params.get("i_ss_a"),
            tau_ms=params.get("tau_ms"),
            e_arranque_j=params.get("e_arranque_j"),
        )

    def registrar_diagnostico(
        self,
        id_motor: Optional[int],
        archivo_csv: str,
        estado_detectado: str,
        confianza_pct: float,
        severidad: str,
        v_ss_v: Optional[float] = None,
        i_pico_a: Optional[float] = None,
        i_ss_a: Optional[float] = None,
        tau_ms: Optional[float] = None,
        e_arranque_j: Optional[float] = None,
    ) -> int:
        """Inserta un registro en `historial_diagnosticos` y retorna el ID creado."""
        query = """
            INSERT INTO historial_diagnosticos (
                id_motor, archivo_csv, estado_detectado, confianza_pct, severidad,
                v_ss_v, i_pico_a, i_ss_a, tau_ms, e_arranque_j
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        with self._conectar() as conn:
            cursor = conn.execute(
                query,
                (
                    id_motor,
                    archivo_csv,
                    estado_detectado,
                    confianza_pct,
                    severidad,
                    v_ss_v,
                    i_pico_a,
                    i_ss_a,
                    tau_ms,
                    e_arranque_j,
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def obtener_historial(self, limite: int = 50) -> List[Dict[str, Any]]:
        """Recupera los últimos diagnósticos ordenados del más reciente al más antiguo."""
        query = """
            SELECT h.id_diagnostico,
                   h.fecha_ensayo,
                   COALESCE(
                       NULLIF(TRIM(COALESCE(m.fabricante, '') || ' ' || COALESCE(m.modelo, '')), ''),
                       'Sin perfil asignado'
                   ) AS motor,
                   h.estado_detectado,
                   h.confianza_pct,
                   h.severidad,
                   h.v_ss_v,
                   h.i_pico_a,
                   h.i_ss_a,
                   h.tau_ms,
                   h.e_arranque_j,
                   h.archivo_csv
            FROM historial_diagnosticos h
            LEFT JOIN motores_industriales m ON h.id_motor = m.id_motor
            ORDER BY h.id_diagnostico DESC
            LIMIT ?;
        """
        with self._conectar() as conn:
            return [dict(row) for row in conn.execute(query, (int(limite),)).fetchall()]


if __name__ == "__main__":
    db = DatabaseManager()
    perfiles = db.listar_perfiles_motores()
    print(f"SQLite inicializado en: {db.db_path}")
    print(f"Motores cargados: {len(perfiles)}")
    for p in perfiles:
        print(f"  [{p.id_motor}] {p.resumen_corto()}")