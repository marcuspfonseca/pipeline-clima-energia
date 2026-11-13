import pytest
import requests
import responses

from src.extract.http_client import HttpClient, NonRetryableHTTPError, RetryableHTTPError

BASE = "https://api.exemplo.com"


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def client(sleeps: list[float]) -> HttpClient:
    # sleep falso: registra as esperas e não trava o teste
    return HttpClient(BASE, min_interval=0, max_attempts=3, sleep=sleeps.append)


@responses.activate
def test_sucesso(client: HttpClient) -> None:
    responses.get(f"{BASE}/dados", json={"ok": True})
    assert client.get_json("dados") == {"ok": True}
    assert len(responses.calls) == 1


@responses.activate
def test_429_depois_200_respeita_retry_after(client: HttpClient, sleeps: list[float]) -> None:
    responses.get(f"{BASE}/dados", status=429, headers={"Retry-After": "7"})
    responses.get(f"{BASE}/dados", json={"ok": True})
    assert client.get_json("dados") == {"ok": True}
    assert len(responses.calls) == 2
    assert 7.0 in sleeps


@responses.activate
def test_500_esgota_tentativas(client: HttpClient) -> None:
    responses.get(f"{BASE}/dados", status=500)
    with pytest.raises(RetryableHTTPError):
        client.get_json("dados")
    assert len(responses.calls) == 3


@responses.activate
def test_401_falha_sem_retry(client: HttpClient) -> None:
    responses.get(f"{BASE}/dados", status=401, json={"message": "token inválido"})
    with pytest.raises(NonRetryableHTTPError):
        client.get_json("dados")
    assert len(responses.calls) == 1


@responses.activate
def test_timeout_e_retentado(client: HttpClient) -> None:
    responses.get(f"{BASE}/dados", body=requests.Timeout("lento"))
    responses.get(f"{BASE}/dados", json={"ok": True})
    assert client.get_json("dados") == {"ok": True}
    assert len(responses.calls) == 2