"""
Módulo: src/io_bridge/mysql_motor_repo.py
Proyecto: Diagnóstico Inteligente de Motores DC - Expo ICE Impulsa 2026
Descripción:
    Repositorio unificado de acceso al catálogo de motores industriales e
    historial de diagnósticos. Soporta de forma transparente:
      - Motor SQLite3 portátil ('engine': 'sqlite' en database/db_config.json, por defecto).
      - Servidor MySQL Workbench ('engine': 'mysql' en database/db_config.json).
    Mantiene compatibilidad total con la interfaz esperada por src/ui/motor_spec_panel.py.
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

RAIZ_PROYECTO = Path(__file__).resolve().parents[2]

try:
    from src.core.db_manager import DatabaseManager
    from src.core.motor_profile import MotorSpecProfile
except ImportError:
    sys.path.append(str(RAIZ_PROYECTO))
    from src.core.db_manager import DatabaseManager
    from src.core.motor_profile import MotorSpecProfile

try:
    import mysql.connector
    from mysql.connector import Error as MySQLError

    MYSQL_DRIVER_AVAILABLE = True
except ImportError:
    mysql = None
    MySQLError = Exception
    MYSQL_DRIVER_AVAILABLE = False


class MySQLMotorRepository:
    """
    Gestor de consultas e inserciones del catálogo de motores DC.
    Enruta automáticamente a SQLite (DatabaseManager) o MySQL Workbench
    según 'database/db_config.json'.
    """

    def __init__(self, config_path: Optional[str | Path] = None):
        self.config_path = (
            Path(config_path)
            if config_path is not None
            else RAIZ_PROYECTO / "database" / "db_config.json"
        )
        self.config: Dict[str, Any] = {
            "engine": "sqlite",
            "db_path": "database/ice2026_motores.db",
            "schema_path": "database/schema_motores_dc.sql",
            "auto_init": True,
            "host": "127.0.0.1",
            "port": 3306,
            "user": "root",
            "password": "",
            "database": "ice2026_motores_db",
        }
        self._sqlite_db: Optional[DatabaseManager] = None
        self.cargar_configuracion()

    @property
    def engine(self) -> str:
        return str(self.config.get("engine", "sqlite")).strip().lower()

    def cargar_configuracion(self) -> None:
        if self.config_path.exists():
            try:
                data = json.loads(self.config_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self.config.update(data)
            except Exception:
                pass

        if self.engine == "sqlite":
            self._sqlite_db = DatabaseManager(config_path=self.config_path)
        else:
            self._sqlite_db = None

    def _get_sqlite(self) -> DatabaseManager:
        if self._sqlite_db is None:
            self._sqlite_db = DatabaseManager(config_path=self.config_path)
        return self._sqlite_db

    def _conectar_mysql(self):
        if not MYSQL_DRIVER_AVAILABLE:
            raise RuntimeError(
                "Driver 'mysql-connector-python' no instalado. "
                "Ejecuta: pip install mysql-connector-python (o usa 'engine': 'sqlite' en db_config.json)."
            )
        return mysql.connector.connect(
            host=self.config.get("host", "127.0.0.1"),
            port=int(self.config.get("port", 3306)),
            user=self.config.get("user", "root"),
            password=self.config.get("password", ""),
            database=self.config.get("database", "ice2026_motores_db"),
            connection_timeout=4,
        )

    # ==========================================================================
    # VERIFICACIÓN DE ENLACE
    # ==========================================================================
    def probar_conexion(self) -> Tuple[bool, str]:
        """Verifica enlace activo con el repositorio configurado (SQLite o MySQL)."""
        if self.engine == "sqlite":
            try:
                db = self._get_sqlite()
                total = len(db.listar_perfiles_motores())
                return True, f"SQLite activo ({db.db_path.name}) | {total} motor(es) registrados."
            except Exception as exc:
                return False, f"Error en base de datos SQLite: {exc}"

        conn = None
        cur = None
        try:
            conn = self._conectar_mysql()
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM motores_industriales")
            row = cur.fetchone()
            total = int(row[0]) if row else 0
            return True, f"Conectado a MySQL ({self.config['database']}) | {total} motor(es) registrados."
        except Exception as exc:
            return False, f"Sin conexión MySQL: {exc}"
        finally:
            if cur is not None:
                cur.close()
            if conn is not None and conn.is_connected():
                conn.close()

    # ==========================================================================
    # CONSULTA E INSERCIÓN DE MOTORES
    # ==========================================================================
    def listar_motores(self) -> List[MotorSpecProfile]:
        """Obtiene todos los motores reales registrados en motores_industriales."""
        if self.engine == "sqlite":
            return self._get_sqlite().listar_perfiles_motores()

        conn = None
        cur = None
        try:
            conn = self._conectar_mysql()
            cur = conn.cursor(dictionary=True)
            cur.execute(
                """
                SELECT id_motor, fabricante, modelo, v_nom_min, v_nom_max, v_nom,
                       rw_ohm, l_mh, i_vacio_a, i_nominal_a, i_stall_a, rpm_nominal,
                       fuente_datasheet
                FROM motores_industriales
                ORDER BY v_nom ASC, fabricante ASC, modelo ASC
                """
            )
            filas = cur.fetchall()
        finally:
            if cur is not None:
                cur.close()
            if conn is not None and conn.is_connected():
                conn.close()

        perfiles: List[MotorSpecProfile] = []
        for f in filas:
            perfiles.append(
                MotorSpecProfile(
                    id_motor=f["id_motor"],
                    fabricante=f["fabricante"] or "",
                    modelo=f["modelo"] or "Sin modelo",
                    v_nom_min=float(f["v_nom_min"]) if f["v_nom_min"] is not None else None,
                    v_nom_max=float(f["v_nom_max"]) if f["v_nom_max"] is not None else None,
                    v_nom=float(f["v_nom"]) if f["v_nom"] is not None else None,
                    rw_ohm=float(f["rw_ohm"]) if f["rw_ohm"] is not None else None,
                    l_mh=float(f["l_mh"]) if f["l_mh"] is not None else None,
                    i_vacio_a=float(f["i_vacio_a"]) if f["i_vacio_a"] is not None else None,
                    i_nominal_a=float(f["i_nominal_a"]) if f["i_nominal_a"] is not None else None,
                    i_stall_a=float(f["i_stall_a"]) if f["i_stall_a"] is not None else None,
                    rpm_nominal=int(f["rpm_nominal"]) if f["rpm_nominal"] is not None else None,
                    fuente_datos=f.get("fuente_datasheet") or "MYSQL_WORKBENCH",
                )
            )
        return perfiles

    def insertar_motor(
        self,
        profile: MotorSpecProfile,
        aplicacion: str = "",
        fuente_datasheet: str = "Registro manual desde aplicación",
    ) -> int:
        """Registra o actualiza un motor verificado por ficha técnica y devuelve su ID."""
        v_nom_val = profile.voltaje_referencia()
        if v_nom_val is None or v_nom_val <= 0:
            raise ValueError("Debes indicar al menos el voltaje nominal (V_nom) del motor.")

        fabricante = (profile.fabricante or "").strip() or "No especificado"
        modelo = (profile.modelo or "").strip()
        if not modelo or modelo == "No especificado":
            raise ValueError("Indica el código o modelo del motor según su ficha técnica.")

        if self.engine == "sqlite":
            perfil_limpio = MotorSpecProfile(
                id_motor=profile.id_motor,
                fabricante=fabricante,
                modelo=modelo,
                v_nom_min=profile.v_nom_min,
                v_nom_max=profile.v_nom_max,
                v_nom=v_nom_val,
                rw_ohm=profile.rw_ohm,
                l_mh=profile.l_mh,
                i_vacio_a=profile.i_vacio_a,
                i_nominal_a=profile.i_nominal_a,
                i_stall_a=profile.i_stall_a,
                rpm_nominal=profile.rpm_nominal,
                fuente_datos=fuente_datasheet,
            )
            return self._get_sqlite().agregar_motor(
                perfil_limpio, aplicacion_industrial=aplicacion
            )

        conn = None
        cur = None
        try:
            conn = self._conectar_mysql()
            cur = conn.cursor()
            sql = """
                INSERT INTO motores_industriales
                (fabricante, modelo, aplicacion_industrial, v_nom_min, v_nom_max, v_nom,
                 rw_ohm, l_mh, i_vacio_a, i_nominal_a, i_stall_a, rpm_nominal, fuente_datasheet)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    id_motor = LAST_INSERT_ID(id_motor),
                    aplicacion_industrial = COALESCE(VALUES(aplicacion_industrial), aplicacion_industrial),
                    v_nom_min = VALUES(v_nom_min),
                    v_nom_max = VALUES(v_nom_max),
                    rw_ohm = VALUES(rw_ohm),
                    l_mh = VALUES(l_mh),
                    i_vacio_a = VALUES(i_vacio_a),
                    i_nominal_a = VALUES(i_nominal_a),
                    i_stall_a = VALUES(i_stall_a),
                    rpm_nominal = VALUES(rpm_nominal),
                    fuente_datasheet = VALUES(fuente_datasheet)
            """
            valores = (
                fabricante,
                modelo,
                aplicacion.strip() or None,
                profile.v_nom_min,
                profile.v_nom_max,
                v_nom_val,
                profile.rw_ohm,
                profile.l_mh,
                profile.i_vacio_a,
                profile.i_nominal_a,
                profile.i_stall_a,
                profile.rpm_nominal,
                fuente_datasheet,
            )
            cur.execute(sql, valores)
            conn.commit()
            return int(cur.lastrowid)
        finally:
            if cur is not None:
                cur.close()
            if conn is not None and conn.is_connected():
                conn.close()

    # ==========================================================================
    # BITÁCORA DE DIAGNÓSTICOS
    # ==========================================================================
    def registrar_reporte(self, reporte: Any, id_motor: Optional[int] = None) -> int:
        """Persiste un `DiagnosticReport` en `historial_diagnosticos`."""
        if self.engine == "sqlite":
            return self._get_sqlite().registrar_reporte(reporte, id_motor=id_motor)

        params = getattr(reporte, "parametros_clave", {}) or {}
        motor_id_final = id_motor if id_motor is not None else getattr(reporte, "id_motor", None)

        conn = None
        cur = None
        try:
            conn = self._conectar_mysql()
            cur = conn.cursor()
            sql = """
                INSERT INTO historial_diagnosticos (
                    id_motor, archivo_csv, estado_detectado, confianza_pct, severidad,
                    v_ss_v, i_pico_a, i_ss_a, tau_ms, e_arranque_j
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            cur.execute(
                sql,
                (
                    motor_id_final,
                    reporte.archivo,
                    reporte.estado_detectado,
                    float(reporte.confianza_pct),
                    reporte.severidad,
                    params.get("v_ss_v"),
                    params.get("i_pico_suave_a"),
                    params.get("i_ss_a"),
                    params.get("tau_ms"),
                    params.get("e_arranque_j"),
                ),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            if cur is not None:
                cur.close()
            if conn is not None and conn.is_connected():
                conn.close()


# Alias neutro para nuevas importaciones sin acoplar el nombre a MySQL
MotorRepository = MySQLMotorRepository


if __name__ == "__main__":
    repo = MySQLMotorRepository()
    ok, mensaje = repo.probar_conexion()
    print(f"Estado enlace [{repo.engine.upper()}]: {ok} -> {mensaje}")
    if ok:
        for m in repo.listar_motores():
            print(f"  [{m.id_motor}] {m.resumen_corto()}")