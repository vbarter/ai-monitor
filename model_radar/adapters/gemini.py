"""Gemini list models: GET https://generativelanguage.googleapis.com/v1beta/models

The key is sent as `x-goog-api-key`, not on the query string.
"""

from __future__ import annotations

from urllib.parse import urlencode

from model_radar.adapters.base import Adapter, CollectResult, Entry, FetchContext, failure
from model_radar.http import RemoteError, read_json

URL = "https://generativelanguage.googleapis.com/v1beta/models"
MAX_PAGES = 10


class GeminiAdapter(Adapter):
    source_id = "gemini:api"
    adapter = "gemini"
    provider = "google"
    url = URL
    diff_mode = "catalog"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def fetch(self, ctx: FetchContext) -> CollectResult:
        headers = {"x-goog-api-key": self.api_key, "Accept": "application/json"}
        entries: list[Entry] = []
        page_token = None
        try:
            for _ in range(MAX_PAGES):
                query = {"pageSize": "100"}
                if page_token:
                    query["pageToken"] = page_token
                page_url = f"{URL}?{urlencode(query)}"
                response = ctx.http.get(page_url, headers)
                payload = read_json(response)
                if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
                    return failure(self.source_id, self.diff_mode, "catalog missing models[]")
                for row in payload["models"]:
                    if not isinstance(row, dict):
                        continue
                    name = str(row.get("name") or "").strip()
                    if not name:
                        continue
                    model_id = name.removeprefix("models/")
                    version = str(row.get("version") or "")
                    entries.append(
                        Entry(
                            provider="google",
                            model_id=model_id,
                            version=version,
                            title=str(row.get("displayName") or model_id),
                            official_url=f"https://ai.google.dev/gemini-api/docs/models/{model_id}",
                            kind="catalog_add",
                            first_party=True,
                        )
                    )
                page_token = payload.get("nextPageToken")
                if not page_token:
                    break
            else:
                return failure(self.source_id, self.diff_mode, "catalog pagination truncated", incomplete=True)
        except RemoteError as exc:
            return failure(self.source_id, self.diff_mode, str(exc))
        if not entries:
            return CollectResult(
                source_id=self.source_id, ok=False, empty=True, error="empty body", diff_mode=self.diff_mode
            )
        return CollectResult(source_id=self.source_id, ok=True, entries=entries, diff_mode=self.diff_mode)
