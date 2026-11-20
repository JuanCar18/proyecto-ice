"""
Paquete src.io_bridge — Puente de entrada/salida y persistencia de catálogo
(SQLite portátil por defecto / MySQL Workbench opcional).
"""

from src.io_bridge.mysql_motor_repo import MotorRepository, MySQLMotorRepository

__all__ = [
    "MotorRepository",
    "MySQLMotorRepository",
]