"""Extrator do Open-Meteo Archive: clima diário (chuva e temperatura) por local."""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

from src import config
from src.extract.bronze import save_raw
from src.extract.http_client import HttpClient

logger = logging.getLogger(__name__)

SOURCE = "open_meteo"


def build_params(lat: float, lon: float, start: date, end: date) -> dict[str, Any]:
    """Monta os parâmetros da chamada /archive para um local e período."""
    return {
        "latitude": lat,
        "longitude": lon,
        "start_date": start.isoformat(),  # formato AAAA-MM-DD exigido pela API
        "end_date": end.isoformat(),
        "daily": ",".join(config.OPEN_METEO_DAILY_VARS),
        "timezone": "America/Sao_Paulo",  # datas no fuso local, para casar com o pregão
    }


def extract(
    start: date,
    end: date,
    *,
    client: HttpClient | None = None,
    raw_dir: Path = config.RAW_DIR,
    locations: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Extrai o clima de cada local e salva o JSON bruto na camada bronze.

    Uma falha em um local NÃO interrompe os demais: o erro é registrado no
    resumo, que depois alimenta o relatório de qualidade.

    Retorna uma lista com o resultado por local, ex.:
        [{"source": "open_meteo", "entity": "furnas", "status": "ok", "path": "..."}]
    """
    if start > end:
        raise ValueError(f"start ({start}) maior que end ({end})")

    locations = locations or config.LOCATIONS
    # Se nenhum cliente foi passado (uso normal), cria um; nos testes, injetamos um
    own_client = client is None
    client = client or HttpClient(
        config.OPEN_METEO_BASE_URL, min_interval=config.OPEN_METEO_MIN_INTERVAL
    )

    results: list[dict[str, Any]] = []
    try:
        for loc in locations:
            params = build_params(float(loc["lat"]), float(loc["lon"]), start, end)
            try:
                payload = client.get_json("archive", params=params)
                path = save_raw(raw_dir, SOURCE, str(loc["id"]), payload, params)
                n_days = len(payload.get("daily", {}).get("time", []))
                logger.info("Open-Meteo %s: %d dias salvos em %s", loc["id"], n_days, path)
                results.append(
                    {"source": SOURCE, "entity": loc["id"], "status": "ok",
                     "records": n_days, "path": str(path)}
                )
            except Exception as exc:  # noqa: BLE001 - queremos seguir para o próximo local
                logger.error("Open-Meteo %s falhou: %s", loc["id"], exc)
                results.append(
                    {"source": SOURCE, "entity": loc["id"], "status": "error",
                     "records": 0, "error": str(exc)}
                )
    finally:
        if own_client:
            client.close()  # só fecha o cliente se fomos nós que criamos

    return results


if __name__ == "__main__":
    # Execução manual: python -m src.extract.open_meteo
    from datetime import timedelta

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    fim = date.today() - timedelta(days=5)   # o arquivo histórico tem ~5 dias de atraso
    inicio = fim - timedelta(days=90)        # janela de ~3 meses (mesma da brapi free)
    for r in extract(inicio, fim):
        print(r)