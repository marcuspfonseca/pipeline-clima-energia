"""Configurações centrais do pipeline: credenciais, caminhos e parâmetros das fontes."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Credenciais (vêm SEMPRE do ambiente)
BRAPI_TOKEN: str | None = os.getenv("BRAPI_TOKEN")

# Caminhos
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"   # camada bronze (JSON bruto)
OUTPUT_DIR = PROJECT_ROOT / "output"      # camada final (Parquet + relatório DQ)

# Open-Meteo (clima)
OPEN_METEO_BASE_URL = "https://archive-api.open-meteo.com/v1"
OPEN_METEO_MIN_INTERVAL = 0.5  # segundos entre chamadas (limite oficial: 600/min)
OPEN_METEO_DAILY_VARS = [
    "precipitation_sum",
    "temperature_2m_max",
    "temperature_2m_min",
]

# Locais monitorados: cidades + reservatórios de hidrelétricas em MG
LOCATIONS: list[dict[str, str | float]] = [
    {"id": "belo_horizonte", "nome": "Belo Horizonte", "lat": -19.92, "lon": -43.94},
    {"id": "sete_lagoas", "nome": "Sete Lagoas", "lat": -19.47, "lon": -44.25},
    {"id": "tres_marias", "nome": "Reservatório Três Marias", "lat": -18.21, "lon": -45.26},
    {"id": "furnas", "nome": "Reservatório Furnas", "lat": -20.67, "lon": -46.32},
]