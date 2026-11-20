-- ============================================================================
-- Archivo: database/schema_motores_dc_sqlite.sql
-- Proyecto: Diagnostico Inteligente de Motores DC - Expo ICE Impulsa 2026
-- Motor BD: SQLite 3 (Nativo en Python, 100% portatil sin servidor externo)
-- Descripcion: Esquema relacional para catalogo de motores DC industriales
--              reales (verificados desde datasheets oficiales) y bitacora
--              de diagnosticos.
-- ============================================================================

PRAGMA foreign_keys = ON;

-- 1. Catalogo de motores DC reales (poblado desde datasheets oficiales)
CREATE TABLE IF NOT EXISTS motores_industriales (
    id_motor INTEGER PRIMARY KEY AUTOINCREMENT,
    fabricante TEXT NOT NULL,
    modelo TEXT NOT NULL,
    aplicacion_industrial TEXT NULL,
    v_nom_min REAL NULL,
    v_nom_max REAL NULL,
    v_nom REAL NOT NULL,
    rw_ohm REAL NULL,
    l_mh REAL NULL,
    i_vacio_a REAL NULL,
    i_nominal_a REAL NULL,
    i_stall_a REAL NULL,
    rpm_nominal INTEGER NULL,
    fuente_datasheet TEXT NULL,
    fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (fabricante, modelo, v_nom)
);

-- 2. Bitacora de diagnosticos realizados desde la aplicacion Python
CREATE TABLE IF NOT EXISTS historial_diagnosticos (
    id_diagnostico INTEGER PRIMARY KEY AUTOINCREMENT,
    id_motor INTEGER NULL,
    archivo_csv TEXT NOT NULL,
    estado_detectado TEXT NOT NULL,
    confianza_pct REAL NOT NULL,
    severidad TEXT NOT NULL,
    v_ss_v REAL NULL,
    i_pico_a REAL NULL,
    i_ss_a REAL NULL,
    tau_ms REAL NULL,
    e_arranque_j REAL NULL,
    fecha_ensayo TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_diag_motor
        FOREIGN KEY (id_motor) REFERENCES motores_industriales(id_motor)
        ON DELETE SET NULL
);

-- ============================================================================
-- POBLADO DE CATALOGO REAL VERIFICADO DESDE DATASHEETS OFICIALES
-- ============================================================================
INSERT OR IGNORE INTO motores_industriales (
    fabricante, modelo, aplicacion_industrial,
    v_nom_min, v_nom_max, v_nom,
    rw_ohm, l_mh, i_vacio_a, i_nominal_a, i_stall_a,
    rpm_nominal, fuente_datasheet
) VALUES
(
    'Faulhaber', '2342S006CR', 'Actuador de precision y dosificacion medica',
    3.00, 6.00, 6.00,
    0.3620, 0.0131, 0.1570, 2.6100, 16.5746,
    6890, 'https://eshop.faulhaber.com/en/2342S006CR/2342S006CR'
),
(
    'Faulhaber', '2224U006SR', 'Instrumentacion portatil y microactuadores',
    3.00, 6.00, 6.00,
    1.9500, 0.0460, 0.0289, 0.7000, 3.0769,
    6350, 'https://www.faulhaber.com/fileadmin/Import/Media/EN_TI_DC-MOTORS.pdf'
),
(
    'Maxon', 'RE 25 (118742)', 'Posicionamiento optico y robotica ligera',
    6.00, 12.00, 9.00,
    1.2300, 0.1350, 0.0387, 1.5000, 7.3171,
    4220, 'https://www.maxongroup.com/maxon/view/product/118742'
),
(
    'Maxon', 'RE 25 (118743)', 'Servomecanismos y robotica industrial',
    6.00, 15.00, 12.00,
    2.1800, 0.2380, 0.0263, 1.2400, 5.5000,
    3800, 'https://www.maxongroup.com/maxon/view/product/118743'
),
(
    'Maxon', 'RE 25 (339150)', 'Actuador lineal y automatizacion',
    6.00, 15.00, 12.00,
    0.5170, 0.0573, 0.0681, 2.4500, 23.2000,
    8310, 'https://www.maxongroup.com/maxon/view/product/motor/dcmotor/re/re25/339150'
),
(
    'Pololu', '37Dx52L 12V (Item 4750)', 'Banda transportadora y traccion movil',
    6.00, 12.00, 12.00,
    2.1818, 2.3000, 0.2000, 1.1000, 5.5000,
    10000, 'https://www.pololu.com/category/271/12v-37d-metal-gearmotors'
),
(
    'Mabuchi', 'RS-555PH-3550', 'Bomba peristaltica y herramienta electromecanica',
    7.00, 12.00, 12.00,
    1.2000, 1.5000, 0.1000, 1.2500, 10.1500,
    4970, 'https://robotics.caltech.edu/~me72/resources/motors.html'
),
(
    'Ametek Pittman', '9234S004', 'Servosistema de control industrial',
    6.00, 12.00, 12.00,
    0.8300, 0.6300, 0.1400, 2.5800, 14.4578,
    6151, 'https://hades.mech.northwestern.edu/images/3/3c/Pittman-ELCOM-SL-4443S013.pdf'
),
(
    'Maxon', 'RE 25 (118752)', 'Robotica industrial y posicionamiento CNC',
    12.00, 24.00, 24.00,
    2.3200, 0.2380, 0.0369, 1.1600, 10.4000,
    8330, 'https://www.maxongroup.com/maxon/view/product/motor/dcmotor/re/re25/118752'
),
(
    'Faulhaber', '2642W024CR', 'Valvula proporcional y automatizacion industrial',
    12.00, 24.00, 24.00,
    5.7900, 0.1400, 0.0575, 0.8500, 4.1451,
    5200, 'https://eshop.faulhaber.com/en/2642W024CR/2642W024CR'
),
(
    'Pololu', '37Dx52L 24V (Item 4760)', 'Banda transportadora pesada y actuador',
    12.00, 24.00, 24.00,
    8.0000, 8.5000, 0.1000, 0.6000, 3.0000,
    10000, 'https://www.pololu.com/category/272/24v-37d-metal-gearmotors'
);