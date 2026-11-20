# Diagnóstico Inteligente de Motores DC — Proyecto Kb (Expo ICE Impulsa 2026)

Sistema de adquisición, análisis de transitorios eléctricos de arranque (*Inrush*) y diagnóstico automático de fallas en motores de corriente directa mediante modelado dinámico RL de 1er orden, calibración híbrida con catálogo SQLite e Inteligencia Artificial ligera.

## Estructura del Repositorio

```text
INTERFAZ/
├── main.py                        # Punto de entrada de la estación de análisis (PyQt5)
├── requirements.txt               # Dependencias exactas de Python
├── .gitignore                     # Exclusiones de entorno virtual, BD local y temporales
├── database/
│   ├── db_config.json             # Configuración portátil del motor SQLite3
│   ├── schema_motores_dc.sql      # Esquema relacional + catálogo de 11 motores reales
│   └── ice2026_motores.db         # Base de datos SQLite autogenerada (ignorada en Git)
├── datasets/
│   ├── raw/                       # Capturas CSV crudas del ESP32 + INA219
│   └── processed/                 # Tablas consolidadas de características extraídas
├── models/
│   └── motor_rf_classifier.joblib # Modelo Random Forest serializado (v2 con baseline)
└── src/
    ├── core/
    │   ├── motor_profile.py       # Perfil nominal de placa / catálogo (MotorSpecProfile)
    │   ├── transient_processor.py # Procesamiento DSP, filtrado y extracción de 20 métricas
    │   ├── ai_engine.py           # Motor IA (Random Forest + Reglas Físicas RL + Placa)
    │   └── db_manager.py          # Gestor SQLite3 de catálogo e historial de diagnósticos
    ├── io_bridge/                 # Comunicación Serial / adquisición desde hardware
    └── ui/                        # Interfaz gráfica CAD/CAE (PyQt5 + Matplotlib)

