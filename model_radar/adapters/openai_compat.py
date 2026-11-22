"""Shared parser for OpenAI-style `{data: [{id, ...}]}` list payloads."""

from __future__ import annotations

from model_radar.adapters.base import Entry
from model_radar.http import RemoteError


def parse_model_list(payload: object) -> list[dict]:
    if not isinstance(payload, dict):
        raise RemoteError("catalog must be a JSON object")
    if payload.get("error") and "data" not in payload:
        err = payload["error"]
        message = err.get("message") if isinstance(err, dict) else str(err)
        raise RemoteError(str(message))
    data = payload.get("data")
    if not isinstance(data, list):
        raise RemoteError("catalog missing data[]")
    return [row for row in data if isinstance(row, dict)]


def entries_from_model_list(
    rows: list[dict],
    *,
    provider: str,
    url_for,
    first_party: bool = True,
) -> list[Entry]:
    entries: list[Entry] = []
    for row in rows:
        model_id = str(row.get("id") or "").strip()
        if not model_id:
            continue
        created = row.get("created")
        entries.append(
            Entry(
                provider=provider,
                model_id=model_id,
                title=str(row.get("display_name") or row.get("id") or model_id),
                official_url=url_for(model_id),
                kind="catalog_add",
                first_party=first_party,
                published_at=str(created) if created is not None else None,
            )
        )
    return entries
