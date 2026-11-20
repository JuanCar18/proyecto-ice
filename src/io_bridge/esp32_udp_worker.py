import csv
import socket
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
from PyQt5.QtCore import QObject, pyqtSignal


class ESP32UdpWorker(QObject):
    """Hilo receptor UDP con recuperación NACK, filtro anti-ruido y protección SRAM."""

    telemetria_recibida = pyqtSignal(str, float, float, str, float)
    progreso_sram = pyqtSignal(int, int)
    captura_cruda_lista = pyqtSignal(dict)
    log_mensaje = pyqtSignal(str)
    estado_entrada_cambiado = pyqtSignal(bool)

    UDP_PORT_DATOS = 5000
    UDP_PORT_CMD = 5001
    MAX_MUESTRAS_SEGURIDAD_PC = 20000

    def __init__(self, carpeta_capturas: str = "capturas_esp32", parent=None):
        super().__init__(parent)
        self.carpeta_capturas = Path(carpeta_capturas)
        self.carpeta_capturas.mkdir(parents=True, exist_ok=True)

        self._corriendo = False
        self._hilo = None
        self._lock = threading.Lock()

        self._muestras: Dict[int, Tuple[int, float, float]] = {}
        self._ip_esp32 = None
        self._auto_armado = False
        self._ultimo_paquete_ts = 0.0
        self._ultimo_status_ts = 0.0
        self._motor_en_reposo = True
        self._en_reposo_desde = time.time()

        self._fin_recibido = False
        self._fin_recibido_ts = 0.0
        self._total_esperado_fin = 0
        self._saturacion_pendiente = False
        self._motivo_sat_pendiente = ""
        self._heap_antes_pend = 0
        self._heap_despues_pend = 0
        self._intentos_nack = 0

    def activar_entrada(self):
        if self._corriendo:
            return
        self._corriendo = True
        with self._lock:
            self._muestras.clear()
            self._auto_armado = False
            self._fin_recibido = False
            self._intentos_nack = 0
        self._hilo = threading.Thread(target=self._bucle_udp, daemon=True)
        self._hilo.start()
        self.estado_entrada_cambiado.emit(True)
        self.log_mensaje.emit("[ESP32] Entrada UDP activada en puerto 5000.")

    def desactivar_entrada(self):
        self._corriendo = False
        self.estado_entrada_cambiado.emit(False)

    def enviar_comando_esp32(self, comando: str):
        try:
            sock_cmd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock_cmd.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            destino = self._ip_esp32 if self._ip_esp32 else "255.255.255.255"
            sock_cmd.sendto(comando.encode("utf-8"), (destino, self.UDP_PORT_CMD))
            sock_cmd.close()
        except Exception as e:
            self.log_mensaje.emit(f"[ESP32 CMD ERROR] {e}")

    @staticmethod
    def _agrupar_rangos_faltantes(faltantes: List[int]) -> List[Tuple[int, int]]:
        if not faltantes:
            return []
        rangos = []
        ini = faltantes[0]
        prev = faltantes[0]
        for s in faltantes[1:]:
            if s == prev + 1:
                prev = s
            else:
                rangos.append((ini, prev))
                ini = s
                prev = s
        rangos.append((ini, prev))
        return rangos

    def _verificar_integridad_o_pedir_nack(self, total_esperado: int) -> bool:
        with self._lock:
            recibidas = set(self._muestras.keys())

        if total_esperado <= 0 and recibidas:
            total_esperado = max(recibidas) + 1

        faltantes = sorted(set(range(total_esperado)) - recibidas)
        if not faltantes:
            return True

        if self._intentos_nack >= 2:
            self.log_mensaje.emit(
                f"[RED] Recuperado el máximo posible ({len(recibidas)}/{total_esperado} muestras)."
            )
            return True

        self._intentos_nack += 1
        rangos = self._agrupar_rangos_faltantes(faltantes)
        self.log_mensaje.emit(
            f"[NACK #{self._intentos_nack}] Recuperando {len(faltantes)} muestras en {len(rangos)} bloque(s)..."
        )
        for ini, fin in rangos[:4]:
            self.enviar_comando_esp32(f"NACK,{ini},{fin}")
            time.sleep(0.015)
        return False

    def _bucle_udp(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 524288)
        sock.bind(("0.0.0.0", self.UDP_PORT_DATOS))
        sock.settimeout(0.15)

        self.enviar_comando_esp32("ARMAR")

        while self._corriendo:
            try:
                data, addr = sock.recvfrom(4096)
                self._ip_esp32 = addr[0]
                texto = data.decode("utf-8", errors="ignore").strip()

                for linea in texto.splitlines():
                    partes = linea.split(",")

                    if partes[0] == "STATUS" and len(partes) >= 4:
                        est_esp = partes[1]
                        v_val = float(partes[2])
                        i_val = float(partes[3])

                        if not self._auto_armado:
                            self.enviar_comando_esp32("ARMAR")
                            self._auto_armado = True
                            self.log_mensaje.emit(f"[ESP32] Enlazado en {self._ip_esp32} y armado automáticamente.")

                        if abs(i_val) < 10.0 and v_val < 1.0:
                            if not self._motor_en_reposo:
                                if (time.time() - self._en_reposo_desde) >= 2.0:
                                    self._motor_en_reposo = True
                                    self.enviar_comando_esp32("RESET")
                                    self.log_mensaje.emit("[ESP32] Motor en reposo (>2 s). Sistema rearmado para nueva prueba.")
                        else:
                            self._en_reposo_desde = time.time()

                        ahora = time.time()
                        if ahora - self._ultimo_status_ts >= 0.35:
                            self._ultimo_status_ts = ahora
                            self.telemetria_recibida.emit(est_esp, v_val, i_val, self._ip_esp32, 2500.0)

                    elif partes[0] == "FIN_SATURACION" and len(partes) >= 5:
                        self._total_esperado_fin = int(partes[1])
                        self._heap_antes_pend = int(partes[2])
                        self._heap_despues_pend = int(partes[3])
                        self._motivo_sat_pendiente = partes[4]
                        self._saturacion_pendiente = True
                        if not self._fin_recibido:
                            self._fin_recibido = True
                            self._fin_recibido_ts = time.time()

                    elif partes[0] == "FIN" and len(partes) >= 2:
                        self._total_esperado_fin = int(partes[1])
                        if not self._fin_recibido:
                            self._fin_recibido = True
                            self._fin_recibido_ts = time.time()

                    elif len(partes) == 4:
                        seq = int(partes[0])
                        t_us = int(partes[1])
                        v_val = float(partes[2])
                        i_val = float(partes[3])

                        with self._lock:
                            if not self._motor_en_reposo and not self._fin_recibido:
                                continue
                            self._muestras[seq] = (t_us, v_val, i_val)
                            self._ultimo_paquete_ts = time.time()
                            n_act = len(self._muestras)

                        if n_act % 500 == 0:
                            self.progreso_sram.emit(n_act, max(15000, self._total_esperado_fin))

                        if n_act >= self.MAX_MUESTRAS_SEGURIDAD_PC:
                            self._finalizar_rafaga(
                                total_esperado=n_act,
                                saturacion_sram=True,
                                motivo_saturacion="LIMITE_PAQUETES_PC_EXCEDIDO",
                            )
                            break

            except socket.timeout:
                with self._lock:
                    n_pend = len(self._muestras)
                    dt_silencio = time.time() - self._ultimo_paquete_ts
                    max_seq = (max(self._muestras.keys()) + 1) if self._muestras else 0

                if n_pend >= 200 and (self._fin_recibido or dt_silencio > 0.45):
                    total_obj = max(self._total_esperado_fin, max_seq)
                    if self._verificar_integridad_o_pedir_nack(total_obj):
                        self._finalizar_rafaga(
                            total_esperado=total_obj,
                            saturacion_sram=self._saturacion_pendiente,
                            motivo_saturacion=self._motivo_sat_pendiente,
                            heap_antes=self._heap_antes_pend,
                            heap_despues=self._heap_despues_pend,
                        )
            except Exception as exc:
                if self._corriendo:
                    self.log_mensaje.emit(f"[ESP32 UDP ERROR] {exc}")

        sock.close()

    def _finalizar_rafaga(
        self,
        total_esperado: int = 0,
        saturacion_sram: bool = False,
        motivo_saturacion: str = "",
        heap_antes: int = 0,
        heap_despues: int = 0,
    ):
        with self._lock:
            if not self._muestras:
                return
            seqs = sorted(self._muestras.keys())
            datos = [self._muestras[s] for s in seqs]
            self._muestras.clear()
            self._fin_recibido = False
            self._saturacion_pendiente = False
            self._intentos_nack = 0

        self.enviar_comando_esp32("ACK_OK")

        n_recibidas = len(datos)
        total_ref = max(total_esperado, n_recibidas)
        integridad_pct = (100.0 * n_recibidas / total_ref) if total_ref > 0 else 100.0

        t_us = np.array([d[0] for d in datos], dtype=np.int64)
        v_arr = np.array([d[1] for d in datos], dtype=float)
        i_arr = np.array([d[2] for d in datos], dtype=float)

        deltas = np.diff(t_us) & 0xFFFFFFFF
        t_s = np.concatenate(([0.0], np.cumsum(deltas) / 1e6))

        # Validar que hubo voltaje y corriente reales (descarta disparos por ruido en 0 V)
        if float(np.percentile(v_arr, 90)) < 1.5 or float(np.max(i_arr)) < 45.0:
            self.log_mensaje.emit("[ESP32] Ráfaga descartada (sin voltaje/corriente válida de motor).")
            with self._lock:
                self._motor_en_reposo = True
            self.enviar_comando_esp32("RESET")
            return

        with self._lock:
            self._motor_en_reposo = False
            self._en_reposo_desde = time.time()

        # Recortar cola apagada solo si un promedio móvil de 50 ms cae a V < 0.8 V e I < 5 mA
        if len(v_arr) > 400:
            win = 125  # 50 ms a 2,500 Hz
            v_mov = np.convolve(v_arr, np.ones(win) / win, mode="same")
            i_mov = np.convolve(i_arr, np.ones(win) / win, mode="same")
            apagado = np.where((t_s > 0.35) & (v_mov < 0.8) & (i_mov < 5.0))[0]
            if len(apagado) > 0:
                idx_corte = int(apagado[0])
                t_s = t_s[:idx_corte]
                v_arr = v_arr[:idx_corte]
                i_arr = i_arr[:idx_corte]

        if not saturacion_sram and n_recibidas >= 14500 and len(i_arr) >= 1000:
            cola_i = i_arr[-600:]
            deriva_cola = abs(float(np.mean(cola_i[-150:]) - np.mean(cola_i[:150])))
            if deriva_cola > 12.0:
                saturacion_sram = True
                motivo_saturacion = "NO_ESTABILIZADO_DETECTADO_EN_COLA"

        self.log_mensaje.emit(
            f"[CAPTURA COMPLETA] {n_recibidas}/{total_ref} muestras recibidas (Integridad: {integridad_pct:.1f}%)."
        )
        if saturacion_sram:
            self.log_mensaje.emit(
                f"[PROTECCIÓN SRAM ESP32] Motor no estabilizado ({motivo_saturacion}). RAM liberada."
            )

        datos_proc = {
            "t_plot": t_s,
            "v_plot": v_arr,
            "i_plot": i_arr,
            "integridad_pct": integridad_pct,
            "muestras_recibidas": n_recibidas,
            "muestras_esperadas": total_ref,
            "saturacion_sram": saturacion_sram,
            "motivo_saturacion": motivo_saturacion,
            "heap_antes": heap_antes,
            "heap_despues": heap_despues,
        }
        self.captura_cruda_lista.emit(datos_proc)

    def escribir_csv_en_ruta(self, ruta_csv: Path, datos_proc: dict, etiqueta: str):
        ruta_csv.parent.mkdir(parents=True, exist_ok=True)
        with open(ruta_csv, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Tiempo(s)", "Voltaje(V)", "Corriente(mA)", "Estado"])
            for ts, vv, ii in zip(datos_proc["t_plot"], datos_proc["v_plot"], datos_proc["i_plot"]):
                writer.writerow([f"{ts:.6f}", f"{vv:.3f}", f"{ii:.1f}", etiqueta])

    def guardar_csv_por_diagnostico(self, datos_proc: dict, estado_detectado: str) -> Path:
        carpeta_clase = self.carpeta_capturas / estado_detectado
        carpeta_clase.mkdir(parents=True, exist_ok=True)
        marca = datetime.now().strftime("%Y%m%d_%H%M%S")
        nombre = f"{estado_detectado.lower()}_{marca}.csv"
        ruta_final = carpeta_clase / nombre
        self.escribir_csv_en_ruta(ruta_final, datos_proc, estado_detectado)
        return ruta_final