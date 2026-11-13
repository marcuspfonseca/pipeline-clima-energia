"""
Cliente HTTP resiliente usado por todos os extratores (Open-Meteo, brapi, BCB).

Responsabilidades:
  1. Timeout em toda requisição (nunca ficar travado esperando a API).
  2. Rate-limit: intervalo mínimo entre chamadas, para não estourar a cota da API.
  3. Retry com backoff exponencial em erros transitórios (429, 5xx, timeout, queda de rede).
  4. Falha imediata em erros definitivos (401, 404...), onde tentar de novo não adianta.
  5. Logs sem expor credenciais.
"""
# Permite usar o nome da classe como type hint dentro dela mesma (ex.: -> HttpClient)
from __future__ import annotations

import logging
import time
from typing import Any, Callable

import requests
from tenacity import (
    RetryCallState,             # objeto com o "estado" da tentativa atual (nº, exceção, etc.)
    Retrying,                   # controlador de retentativas usado num laço for
    retry_if_exception_type,    # condição: só repete se a exceção for de certos tipos
    stop_after_attempt,         # condição de parada: número máximo de tentativas
    wait_exponential_jitter,    # espera crescente (1s, 2s, 4s...) com um ruído aleatório
)

# Logger do módulo: a configuração (nível, formato) é feita no pipeline principal
logger = logging.getLogger(__name__)

# Status HTTP que indicam problema temporário do servidor ou excesso de requisições.
# 429 = Too Many Requests | 500/502/503/504 = erros do lado do servidor
RETRYABLE_STATUS: set[int] = {429, 500, 502, 503, 504}


# ---------------------------------------------------------------------------
# Exceções próprias: separar "vale tentar de novo" de "não adianta insistir"
# ---------------------------------------------------------------------------
class RetryableHTTPError(Exception):
    """Erro transitório (429/5xx): o tenacity vai tentar de novo."""

    def __init__(self, status_code: int, url: str, retry_after: float | None = None) -> None:
        super().__init__(f"HTTP {status_code} em {url}")
        self.status_code = status_code
        # Quantos segundos a API pediu para esperar (header Retry-After), se informou
        self.retry_after = retry_after


class NonRetryableHTTPError(Exception):
    """Erro definitivo (400/401/403/404...): falha na hora, sem retry."""

    def __init__(self, status_code: int, url: str, body: str) -> None:
        # Guarda só os 200 primeiros caracteres da resposta para não poluir o log
        super().__init__(f"HTTP {status_code} em {url}: {body[:200]}")
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Rate-limiter
# ---------------------------------------------------------------------------
class RateLimiter:
    """Garante um intervalo mínimo (em segundos) entre chamadas consecutivas.

    Ex.: min_interval=1.0 -> no máximo 1 requisição por segundo.
    """

    def __init__(self, min_interval: float, sleep: Callable[[float], None] = time.sleep) -> None:
        self.min_interval = min_interval
        # A função de espera é injetável: nos testes trocamos por uma função falsa
        # para o teste não ficar parado esperando de verdade.
        self._sleep = sleep
        self._last_call: float | None = None  # instante da última chamada

    def wait(self) -> None:
        # time.monotonic() é um relógio que só anda pra frente (não muda com
        # ajuste de horário do sistema), ideal para medir intervalos.
        if self._last_call is not None:
            elapsed = time.monotonic() - self._last_call
            if elapsed < self.min_interval:
                # Dorme só o que falta para completar o intervalo mínimo
                self._sleep(self.min_interval - elapsed)
        self._last_call = time.monotonic()


def _parse_retry_after(value: str | None) -> float | None:
    """Converte o header Retry-After em segundos.

    O header pode vir como número ("7") ou como data HTTP ("Wed, 21 Oct 2026 07:28:00 GMT").
    Tratamos só o caso numérico; no outro, retornamos None e o backoff padrão assume.
    """
    if not value:
        return None
    try:
        return max(float(value), 0.0)  # max(...) evita valor negativo
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Cliente principal
# ---------------------------------------------------------------------------
class HttpClient:
    """Cliente GET que retorna JSON, com rate-limit e retentativas.

    Uso:
        with HttpClient("https://api.exemplo.com", min_interval=1.0) as client:
            dados = client.get_json("caminho", params={"x": 1})
    """

    def __init__(
        self,
        base_url: str,
        *,  # tudo depois do * precisa ser passado pelo nome (deixa as chamadas mais legíveis)
        min_interval: float = 0.2,                 # segundos entre chamadas (rate-limit)
        timeout: tuple[float, float] = (10.0, 30.0),  # (tempo p/ conectar, tempo p/ ler a resposta)
        max_attempts: int = 5,                     # total de tentativas, contando a primeira
        max_backoff: float = 30.0,                 # teto da espera entre tentativas
        headers: dict[str, str] | None = None,     # ex.: {"Authorization": "Bearer <token>"}
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")  # remove "/" final para não gerar "//" na URL
        self.timeout = timeout
        self.max_attempts = max_attempts
        self._sleep = sleep
        self._rate_limiter = RateLimiter(min_interval, sleep)
        # Backoff exponencial: 1s, 2s, 4s, 8s... até max_backoff, com jitter (ruído
        # aleatório) para várias execuções não baterem na API no mesmo instante.
        self._backoff = wait_exponential_jitter(initial=1, max=max_backoff)

        # Session reaproveita a conexão TCP entre chamadas (mais rápido) e
        # mantém os headers configurados uma vez só.
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "pipeline-clima-energia/1.0"})
        if headers:
            self.session.headers.update(headers)

    def _wait(self, retry_state: RetryCallState) -> float:
        """Decide quanto esperar antes da próxima tentativa.

        Se a API mandou Retry-After, respeitamos esse valor; senão, usamos o backoff.
        """
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        if isinstance(exc, RetryableHTTPError) and exc.retry_after is not None:
            return exc.retry_after
        return self._backoff(retry_state)

    def _log_retry(self, retry_state: RetryCallState) -> None:
        """Chamado pelo tenacity antes de cada espera: registra a falha no log."""
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        logger.warning(
            "Tentativa %d/%d falhou (%s). Nova tentativa em %.1fs",
            retry_state.attempt_number,
            self.max_attempts,
            exc,
            retry_state.next_action.sleep if retry_state.next_action else 0,
        )

    def _request(self, url: str, params: dict[str, Any] | None) -> Any:
        """Faz UMA requisição e classifica o resultado (sucesso, retry ou falha definitiva)."""
        self._rate_limiter.wait()  # respeita o intervalo mínimo antes de chamar

        start = time.monotonic()
        resp = self.session.get(url, params=params, timeout=self.timeout)
        elapsed_ms = (time.monotonic() - start) * 1000

        # Loga só a URL base + path, NUNCA os params: se algum dia o token for
        # passado como query string, ele não vaza para o log.
        logger.info("GET %s -> %d (%.0f ms)", url, resp.status_code, elapsed_ms)

        if resp.status_code in RETRYABLE_STATUS:
            # Lança a exceção "retentável": o tenacity captura e tenta de novo
            raise RetryableHTTPError(
                resp.status_code, url, _parse_retry_after(resp.headers.get("Retry-After"))
            )
        if resp.status_code >= 400:
            # Qualquer outro erro 4xx: não adianta repetir (ex.: token inválido)
            raise NonRetryableHTTPError(resp.status_code, url, resp.text)

        return resp.json()  # sucesso: devolve o corpo já convertido de JSON para dict/list

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Método público: monta a URL e executa _request com retentativas."""
        url = f"{self.base_url}/{path.lstrip('/')}" if path else self.base_url

        retrying = Retrying(
            stop=stop_after_attempt(self.max_attempts),  # para depois de N tentativas
            wait=self._wait,                              # quanto esperar entre elas
            # Só repete nesses casos. Timeout e ConnectionError são exceções do
            # próprio requests (API lenta ou rede caiu).
            retry=retry_if_exception_type(
                (RetryableHTTPError, requests.Timeout, requests.ConnectionError)
            ),
            before_sleep=self._log_retry,  # loga antes de cada espera
            sleep=self._sleep,
            reraise=True,  # se esgotar as tentativas, relança o erro original (não um RetryError genérico)
        )

        # Padrão do tenacity: cada volta do laço é uma tentativa. Se o bloco
        # "with" lançar exceção retentável, o tenacity espera e roda de novo;
        # se der certo, o return sai do laço.
        for attempt in retrying:
            with attempt:
                return self._request(url, params)
        raise RuntimeError("inalcançável")  # pragma: no cover (só satisfaz o type checker)

    # -----------------------------------------------------------------------
    # Context manager: permite usar "with HttpClient(...) as client:"
    # e garante que a sessão seja fechada no final, mesmo se der erro.
    # -----------------------------------------------------------------------
    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()