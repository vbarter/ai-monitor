"""Anthropic list models: GET https://api.anthropic.com/v1/models

Paginated with `has_more` / `after_id`. Docs pages are geo-blocked from some
egress; this adapter uses the API only and does not fetch those pages.
"""

from __future__ import annotations

from urllib.parse import urlencode

from model_radar.adapters.base import Adapter, CollectResult, Entry, FetchContext, failure
from model_radar.http import RemoteError, read_json

URL = "https://api.anthropic.com/v1/models"
VERSION = "2023-06-01"
MAX_PAGES = 10


class AnthropicAdapter(Adapter):
    source_id = "anthropic:api"
    adapter = "anthropic"
    provider = "anthropic"
    url = URL
    diff_mode = "catalog"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def fetch(self, ctx: FetchContext) -> CollectResult:
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": VERSION,
            "Accept": "application/json",
        }
        entries: list[Entry] = []
        after_id = None
        try:
            for _ in range(MAX_PAGES):
                query = {"limit": "100"}
                if after_id:
                    query["after_id"] = after_id
                page_url = f"{URL}?{urlencode(query)}"
                response = ctx.http.get(page_url, headers)
                payload = read_json(response)
                if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                    return failure(self.source_id, self.diff_mode, "catalog missing data[]")
                for row in payload["data"]:
                    if not isinstance(row, dict):
                        continue
                    model_id = str(row.get("id") or "").strip()
                    if not model_id:
                        continue
                    entries.append(
                        Entry(
                            provider="anthropic",
                            model_id=model_id,
                            title=str(row.get("display_name") or model_id),
                            official_url="https://www.anthropic.com/news/models",
                            kind="catalog_add",
                            first_party=True,
                            published_at=str(row.get("created_at") or "") or None,
                        )
                    )
                if not payload.get("has_more"):
                    break
                after_id = str(payload.get("last_id") or "")
                if not after_id:
                    return failure(self.source_id, self.diff_mode, "has_more without last_id", incomplete=True)
            else:
                return failure(self.source_id, self.diff_mode, "catalog pagination truncated", incomplete=True)
        except RemoteError as exc:
            return failure(self.source_id, self.diff_mode, str(exc))
        if not entries:
            return CollectResult(
                source_id=self.source_id, ok=False, empty=True, error="empty body", diff_mode=self.diff_mode
            )
        return CollectResult(source_id=self.source_id, ok=True, entries=entries, diff_mode=self.diff_mode)
