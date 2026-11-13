"""Gravação da camada bronze: o JSON da API exatamente como veio + metadados."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Parâmetros que NUNCA podem ir para o disco (se algum dia forem passados na URL)
SENSITIVE_KEYS = {"token", "apikey", "api_key", "key"}


def _sanitize(params: dict[str, Any] | None) -> dict[str, Any]:
    """Remove credenciais dos parâmetros antes de salvar."""
    if not params:
        return {}
    return {k: ("***" if k.lower() in SENSITIVE_KEYS else v) for k, v in params.items()}


def save_raw(
    raw_dir: Path,
    source: str,
    entity: str,
    payload: Any,
    params: dict[str, Any] | None = None,
) -> Path:
    """Salva um payload bruto em data/raw/{source}/{entity}_{timestamp}.json.

    O payload fica dentro de um "envelope" com metadados de rastreabilidade
    (quando, de onde e com quais parâmetros foi extraído), usados depois no
    relatório de qualidade.
    """
    extracted_at = datetime.now(timezone.utc)

    folder = raw_dir / source
    folder.mkdir(parents=True, exist_ok=True)  # cria a pasta se não existir

    # Timestamp no nome evita sobrescrever extrações anteriores
    file_path = folder / f"{entity}_{extracted_at:%Y%m%dT%H%M%S}.json"

    envelope = {
        "source": source,
        "entity": entity,
        "extracted_at": extracted_at.isoformat(),
        "params": _sanitize(params),
        "payload": payload,
    }
    # ensure_ascii=False mantém acentos legíveis no arquivo
    file_path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
    return file_path