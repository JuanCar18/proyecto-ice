#include <WiFi.h>
#include <WiFiUdp.h>
#include <Wire.h>
#include <esp_heap_caps.h>

// ==============================================================================
// 1. CONFIGURACIÓN DE RED Y HARDWARE (ESP32 + INA219 + OPTOACOPLADOR)
// ==============================================================================
const char* ssid = "yo123456";
const char* password = "Tacos0123";

const unsigned int puerto_udp_datos = 5000;
const unsigned int puerto_udp_escucha = 5001;

const int pinOpto = 18;
const int frecuenciaPWM = 5000;
const int resolucionPWM = 8;
const int dutyPWM = 128; // 50% ciclo de trabajo

// ==============================================================================
// 2. CONFIGURACIÓN DE MUESTREO Y PROTECCIÓN DE MEMORIA SRAM
// ==============================================================================
const uint32_t PERIODO_MUESTREO_US = 400;       // 400 us = 2,500 Hz constantes
const int PRE_TRIGGER = 100000UL / PERIODO_MUESTREO_US; // 250 muestras = 100.0 ms previos

const int MAX_MUESTRAS_OBJETIVO = 15000;        // 6.0 s a 2,500 Hz (~120 KB)
const uint32_t HEAP_MINIMO_SEGURIDAD = 36000;   // 36 KB reservados para pila Wi-Fi/UDP

// Criterios de estabilización y detección de saturación
const int16_t UMBRAL_SHUNT_RAW = 50;            // 50 * 0.1 mA = 5.0 mA (shunt 0.1 ohm)
const float TOLERANCIA_ESTAB_MA = 8.0f;         // Banda máxima de variación para régimen estable
const int MUESTRAS_VENTANA_ESTAB = 250;         // 100 ms a 2,500 Hz
const int MUESTRAS_CONFIRMACION_ESTAB = 200;    // 80 ms consecutivos planos

struct __attribute__((packed)) MuestraSRAM {
  uint32_t t_us;
  int16_t shunt_raw;
  uint16_t bus_raw;
};

MuestraSRAM* bufferSRAM = nullptr;
int capacidadActualSRAM = 0;

enum EstadoSistema {
  ESPERANDO_ARMADO,
  ESPERANDO_DISPARO,
  CAPTURANDO_TRANSITORIO,
  SATURACION_PROTEGIDA
};

EstadoSistema estadoActual = ESPERANDO_ARMADO;

WiFiUDP udp;
IPAddress ipPC;
bool pcEnlazada = false;
bool ina219_ok = false;

int idxCircular = 0;
int muestrasPreGuardadas = 0;
int totalCapturadas = 0;
int contadorEstableConsecutivo = 0;
uint32_t ultimoMuestreoUs = 0;
uint32_t ultimoStatusMs = 0;

// ==============================================================================
// 3. GESTIÓN SEGURA DE MEMORIA SRAM (RESERVA Y LIBERACIÓN EXPLÍCITA)
// ==============================================================================
void liberarBufferSRAM(const char* motivo) {
  if (bufferSRAM != nullptr) {
    free(bufferSRAM);
    bufferSRAM = nullptr;
    capacidadActualSRAM = 0;
    Serial.printf("[PROTECCIÓN SRAM] Buffer liberado (%s). Heap libre: %u bytes\n",
                  motivo, ESP.getFreeHeap());
  }
}

bool reservarBufferSRAM() {
  if (bufferSRAM != nullptr) {
    return true;
  }

  uint32_t heapLibre = ESP.getFreeHeap();
  if (heapLibre <= HEAP_MINIMO_SEGURIDAD + 16384) {
    Serial.printf("[ALERTA SRAM] Heap insuficiente para reservar buffer: %u bytes\n", heapLibre);
    return false;
  }

  // Intentar 15,000 -> 12,000 -> 9,000 -> 6,000 muestras respetando el margen Wi-Fi
  const int candidatos[] = {MAX_MUESTRAS_OBJETIVO, 12000, 9000, 6000};
  for (int i = 0; i < 4; i++) {
    int n = candidatos[i];
    size_t bytesReq = (size_t)n * sizeof(MuestraSRAM);
    if (heapLibre > bytesReq + HEAP_MINIMO_SEGURIDAD) {
      bufferSRAM = (MuestraSRAM*)malloc(bytesReq);
      if (bufferSRAM != nullptr) {
        capacidadActualSRAM = n;
        memset(bufferSRAM, 0, bytesReq);
        Serial.printf("[SRAM OK] Reservadas %d muestras (%u bytes). Heap restante: %u bytes\n",
                      capacidadActualSRAM, (unsigned int)bytesReq, ESP.getFreeHeap());
        return true;
      }
    }
  }
  return false;
}

// Ordena cronológicamente el búfer circular de pre-disparo sin usar memoria extra
void ordenarPreTriggerInPlace() {
  if (bufferSRAM == nullptr || muestrasPreGuardadas < PRE_TRIGGER || idxCircular == 0) {
    return;
  }
  auto invertirTramo = [](int ini, int fin) {
    while (ini < fin) {
      MuestraSRAM tmp = bufferSRAM[ini];
      bufferSRAM[ini] = bufferSRAM[fin];
      bufferSRAM[fin] = tmp;
      ini++;
      fin--;
    }
  };
  invertirTramo(0, idxCircular - 1);
  invertirTramo(idxCircular, PRE_TRIGGER - 1);
  invertirTramo(0, PRE_TRIGGER - 1);
}

// ==============================================================================
// 4. LECTURA DIRECTA POR REGISTROS DEL INA219 (I2C 800 kHz, 9-BIT)
// ==============================================================================
void configurarINA219Directo() {
  Wire.beginTransmission(0x40);
  Wire.write(0x00);
  Wire.write(0x38); // MSB: 32V, +-320mV, ADC 9-bit continuo
  Wire.write(0x07); // LSB: Shunt + Bus continuo (84 us por canal)
  Wire.endTransmission();
}

inline void leerINA219Crudo(int16_t &shunt_raw, uint16_t &bus_raw) {
  Wire.beginTransmission(0x40);
  Wire.write(0x01); // Registro Shunt Voltage
  Wire.endTransmission(false);
  Wire.requestFrom((uint8_t)0x40, (uint8_t)2);
  shunt_raw = (Wire.available() >= 2) ? ((int16_t)((Wire.read() << 8) | Wire.read())) : 0;

  Wire.beginTransmission(0x40);
  Wire.write(0x02); // Registro Bus Voltage
  Wire.endTransmission(false);
  Wire.requestFrom((uint8_t)0x40, (uint8_t)2);
  bus_raw = (Wire.available() >= 2) ? ((uint16_t)((Wire.read() << 8) | Wire.read())) : 0;
}

inline float convertirVoltajeV(uint16_t bus_raw) {
  return ((bus_raw >> 3) * 4.0f) / 1000.0f;
}

inline float convertirCorrienteMA(int16_t shunt_raw) {
  return shunt_raw * 0.1f; // Shunt = 0.1 ohm -> 10 uV/LSB = 0.1 mA/LSB
}

// ==============================================================================
// 5. DESCARGA POR RÁFAGA UDP Y EMISIÓN DE DIAGNÓSTICO DE SATURACIÓN
// ==============================================================================
void descargarBufferUDP(bool saturado, const char* motivoSaturacion) {
  if (!pcEnlazada || bufferSRAM == nullptr || totalCapturadas <= 0) {
    liberarBufferSRAM("Descarga sin destino o buffer vacío");
    return;
  }

  uint32_t heapAntes = ESP.getFreeHeap();
  String paquete;
  paquete.reserve(1300);

  for (int i = 0; i < totalCapturadas; i++) {
    MuestraSRAM m = bufferSRAM[i];
    float v = convertirVoltajeV(m.bus_raw);
    float c = convertirCorrienteMA(m.shunt_raw);

    paquete += String(i);
    paquete += ',';
    paquete += String(m.t_us);
    paquete += ',';
    paquete += String(v, 3);
    paquete += ',';
    paquete += String(c, 1);
    paquete += '\n';

    if (paquete.length() >= 1150) {
      udp.beginPacket(ipPC, puerto_udp_datos);
      udp.print(paquete);
      udp.endPacket();
      paquete = "";
      delayMicroseconds(350); // Evita saturar el socket Wi-Fi
    }
  }

  if (paquete.length() > 0) {
    udp.beginPacket(ipPC, puerto_udp_datos);
    udp.print(paquete);
    udp.endPacket();
    delay(2);
  }

  // Liberar memoria SRAM inmediatamente después de transmitir
  liberarBufferSRAM(motivoSaturacion);
  uint32_t heapDespues = ESP.getFreeHeap();

  // Emitir paquete de cierre normal o alerta de saturación/no estabilización
  for (int rep = 0; rep < 3; rep++) {
    udp.beginPacket(ipPC, puerto_udp_datos);
    if (saturado) {
      udp.printf("FIN_SATURACION,%d,%u,%u,%s\n",
                 totalCapturadas, heapAntes, heapDespues, motivoSaturacion);
    } else {
      udp.printf("FIN,%d\n", totalCapturadas);
    }
    udp.endPacket();
    delay(4);
  }
}

void rearmarSistema() {
  if (!reservarBufferSRAM()) {
    estadoActual = SATURACION_PROTEGIDA;
    return;
  }
  idxCircular = 0;
  muestrasPreGuardadas = 0;
  totalCapturadas = 0;
  contadorEstableConsecutivo = 0;
  estadoActual = ESPERANDO_DISPARO;
  Serial.println("[ARMADO] Esperando encendido del motor (> 5.0 mA)...");
}

void procesarComandosUDP() {
  int packetSize = udp.parsePacket();
  if (packetSize <= 0) return;

  ipPC = udp.remoteIP();
  pcEnlazada = true;

  char cmdBuf[64];
  int len = udp.read(cmdBuf, sizeof(cmdBuf) - 1);
  if (len <= 0) return;
  cmdBuf[len] = '\0';
  String cmd = String(cmdBuf);
  cmd.trim();

  if (cmd == "ARMAR" || cmd == "RESET") {
    rearmarSistema();
  } else if (cmd == "DISPARAR" && estadoActual == ESPERANDO_DISPARO && bufferSRAM != nullptr) {
    ordenarPreTriggerInPlace();
    totalCapturadas = muestrasPreGuardadas;
    contadorEstableConsecutivo = 0;
    estadoActual = CAPTURANDO_TRANSITORIO;
    Serial.println("[DISPARO MANUAL] Capturando transitorio...");
  }
}

// ==============================================================================
// 6. SETUP Y BUCLE PRINCIPAL CON PROTECCIÓN CONTRA SATURACIÓN DE SRAM
// ==============================================================================
void setup() {
  Serial.begin(115200);
  delay(200);

  ledcAttach(pinOpto, frecuenciaPWM, resolucionPWM);
  ledcWrite(pinOpto, 0);

  // 1. Conectar Wi-Fi primero para asegurar memoria DRAM contigua y evitar [status=6]
  WiFi.mode(WIFI_STA);
  WiFi.persistent(false);
  WiFi.disconnect(true, true);
  delay(300);
  WiFi.setSleep(false);

  Serial.print("Conectando a Wi-Fi");
  WiFi.begin(ssid, password);

  int intentos = 0;
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
    if (++intentos % 20 == 0) { // Esperar 10 s completos antes de reintentar
      Serial.printf(" [status=%d]\n", WiFi.status());
      WiFi.disconnect(false, true);
      delay(200);
      WiFi.begin(ssid, password);
    }
  }
  Serial.print("\nConectado. IP ESP32: ");
  Serial.println(WiFi.localIP());

  // 2. Inicializar bus I2C (SDA=21, SCL=22) e INA219
  Wire.begin(21, 22);
  Wire.setClock(800000);
  Wire.beginTransmission(0x40);
  if (Wire.endTransmission() == 0) {
    ina219_ok = true;
  } else {
    Wire.setClock(400000);
    Wire.beginTransmission(0x40);
    ina219_ok = (Wire.endTransmission() == 0);
  }
  if (ina219_ok) {
    configurarINA219Directo();
  }

  udp.begin(puerto_udp_escucha);
  rearmarSistema();
}

void loop() {
  ledcWrite(pinOpto, dutyPWM);
  procesarComandosUDP();

  // Telemetría periódica mientras no se está en captura crítica
  if (estadoActual != CAPTURANDO_TRANSITORIO) {
    uint32_t ahoraMs = millis();
    if (ahoraMs - ultimoStatusMs >= 500) {
      ultimoStatusMs = ahoraMs;
      int16_t s_raw = 0;
      uint16_t b_raw = 0;
      if (ina219_ok) leerINA219Crudo(s_raw, b_raw);

      const char* tagEstado = !ina219_ok ? "ERROR_INA219" :
                              (estadoActual == SATURACION_PROTEGIDA ? "SRAM_LIBERADA_SATURACION" : "LISTO");

      udp.beginPacket("255.255.255.255", puerto_udp_datos);
      udp.printf("STATUS,%s,%.3f,%.1f\n", tagEstado, convertirVoltajeV(b_raw), convertirCorrienteMA(s_raw));
      udp.endPacket();

      if (pcEnlazada) {
        udp.beginPacket(ipPC, puerto_udp_datos);
        udp.printf("STATUS,%s,%.3f,%.1f\n", tagEstado, convertirVoltajeV(b_raw), convertirCorrienteMA(s_raw));
        udp.endPacket();
      }
    }
  }

  if (!ina219_ok || bufferSRAM == nullptr) return;

  // Control estricto de frecuencia de muestreo (2,500 Hz)
  uint32_t ahoraUs = micros();
  if (ahoraUs - ultimoMuestreoUs < PERIODO_MUESTREO_US) return;
  ultimoMuestreoUs = ahoraUs;

  int16_t shunt_raw = 0;
  uint16_t bus_raw = 0;
  leerINA219Crudo(shunt_raw, bus_raw);

  if (estadoActual == ESPERANDO_DISPARO) {
    // Búfer circular de pre-disparo (primeros PRE_TRIGGER espacios)
    bufferSRAM[idxCircular] = {ahoraUs, shunt_raw, bus_raw};
    idxCircular = (idxCircular + 1) % PRE_TRIGGER;
    if (muestrasPreGuardadas < PRE_TRIGGER) muestrasPreGuardadas++;

    if (shunt_raw > UMBRAL_SHUNT_RAW) {
      ordenarPreTriggerInPlace();
      totalCapturadas = muestrasPreGuardadas;
      contadorEstableConsecutivo = 0;
      estadoActual = CAPTURANDO_TRANSITORIO;
      Serial.println("[DISPARO] Capturando respuesta transitoria...");
    }
  }
  else if (estadoActual == CAPTURANDO_TRANSITORIO) {
    // 1. Guardia de Heap en tiempo real: evita desbordamiento de RAM
    if (ESP.getFreeHeap() < HEAP_MINIMO_SEGURIDAD) {
      Serial.println("[PROTECCIÓN SRAM] Heap crítico alcanzado. Abortando captura y liberando RAM.");
      descargarBufferUDP(true, "HEAP_BAJO_CRITICO");
      estadoActual = SATURACION_PROTEGIDA;
      return;
    }

    // Guardar muestra en SRAM
    if (totalCapturadas < capacidadActualSRAM) {
      bufferSRAM[totalCapturadas++] = {ahoraUs, shunt_raw, bus_raw};
    }

    // 2. Corte temprano si la corriente ya se estabilizó para liberar antes
    int muestrasPost = totalCapturadas - muestrasPreGuardadas;
    if (muestrasPost > (MUESTRAS_VENTANA_ESTAB + 150)) {
      float iActual = convertirCorrienteMA(shunt_raw);
      float iAnterior = convertirCorrienteMA(bufferSRAM[totalCapturadas - 1 - MUESTRAS_VENTANA_ESTAB].shunt_raw);
      if (fabsf(iActual - iAnterior) <= TOLERANCIA_ESTAB_MA) {
        contadorEstableConsecutivo++;
      } else {
        contadorEstableConsecutivo = 0;
      }

      if (contadorEstableConsecutivo >= MUESTRAS_CONFIRMACION_ESTAB) {
        Serial.printf("[ESTABILIZADO] Motor estable en %d muestras. Descargando y liberando SRAM...\n", totalCapturadas);
        descargarBufferUDP(false, "ESTABILIZACION_NORMAL");
        rearmarSistema();
        return;
      }
    }

    // 3. Límite máximo de SRAM alcanzado SIN estabilización -> Liberar y emitir diagnóstico
    if (totalCapturadas >= capacidadActualSRAM) {
      Serial.println("[PROTECCIÓN SRAM] Límite de búfer alcanzado SIN estabilización. Liberando RAM...");
      descargarBufferUDP(true, "NO_ESTABILIZADO_TIEMPO_MAXIMO");
      estadoActual = SATURACION_PROTEGIDA;
    }
  }
}