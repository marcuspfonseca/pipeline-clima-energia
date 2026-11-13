import json
from datetime import date
from pathlib import Path

import pytest
import responses

from src import config
from src.extract import open_meteo
from src.extract.http_client import HttpClient

URL = f"{config.OPEN_METEO_BASE_URL}/archive"

LOCS = [
    {"id": "bh", "nome": "BH", "lat": -19.92, "lon": -43.94},
    {"id": "sl", "nome": "Sete Lagoas", "lat": -19.47, "lon": -44.25},
]

PAYLOAD = {
    "latitude": -19.92,
    "daily": {
        "time": ["2026-08-01", "2026-08-02"],
        "precipitation_sum": [0.0, 3.2],
        "temperature_2m_max": [27.1, 25.4],
        "temperature_2m_min": [14.0, None],
    },
}


@pytest.fixture
def client() -> HttpClient:
    # sem espera real entre chamadas e no máximo 2 tentativas
    return HttpClient(config.OPEN_METEO_BASE_URL, min_interval=0, max_attempts=2,
                      sleep=lambda s: None)


@responses.activate
def test_salva_bronze_por_local(client: HttpClient, tmp_path: Path) -> None:
    responses.get(URL, json=PAYLOAD)
    responses.get(URL, json=PAYLOAD)

    res = open_meteo.extract(date(2026, 8, 1), date(2026, 8, 2),
                             client=client, raw_dir=tmp_path, locations=LOCS)

    assert [r["status"] for r in res] == ["ok", "ok"]
    assert res[0]["records"] == 2
    saved = json.loads(Path(res[0]["path"]).read_text(encoding="utf-8"))
    assert saved["source"] == "open_meteo"
    assert saved["params"]["start_date"] == "2026-08-01"
    assert saved["payload"] == PAYLOAD


@responses.activate
def test_falha_em_um_local_nao_para_os_outros(client: HttpClient, tmp_path: Path) -> None:
    responses.get(URL, status=400, json={"reason": "coordenada inválida"})
    responses.get(URL, json=PAYLOAD)

    res = open_meteo.extract(date(2026, 8, 1), date(2026, 8, 2),
                             client=client, raw_dir=tmp_path, locations=LOCS)

    assert [r["status"] for r in res] == ["error", "ok"]
    assert "400" in res[0]["error"]


def test_datas_invertidas() -> None:
    with pytest.raises(ValueError):
        open_meteo.extract(date(2026, 9, 1), date(2026, 8, 1))