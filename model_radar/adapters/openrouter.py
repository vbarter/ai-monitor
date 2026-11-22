"""OpenRouter public catalog.

JSON: GET https://openrouter.ai/api/v1/models
RSS is a separate feed adapter so a JSON failure still leaves the feed running.
An empty or error body does not replace the stored snapshot; that rule is
enforced by the integrator when `ok` is false or `entries` is empty.
"""

from __future__ import annotations

from urllib.parse import urljoin

from model_radar.adapters.base import Adapter, CollectResult, Entry, FetchContext, etag_headers, failure
from model_radar.http import RemoteError, read_json

CATALOG_URL = "https://openrouter.ai/api/v1/models"
MAX_PAGES = 20


def _pricing(row: dict) -> dict | None:
    raw = row.get("pricing")
    if not isinstance(raw, dict):
        return None
    kept = {}
    for key in ("prompt", "completion"):
        if raw.get(key) is not None:
            kept[key] = str(raw[key])
    return kept or None


def entries_from_rows(rows: list) -> list[Entry]:
    entries: list[Entry] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        model_id = str(row.get("id") or "").strip()
        if not model_id:
            continue
        slug = str(row.get("canonical_slug") or "").strip()
        version = slug if slug and slug != model_id else ""
        context = row.get("context_length")
        entries.append(
            Entry(
                provider="openrouter",
                model_id=model_id,
                version=version,
                title=str(row.get("name") or model_id),
                official_url=f"https://openrouter.ai/{model_id}",
                kind="catalog_add",
                first_party=False,
                pricing=_pricing(row),
                context_length=int(context) if isinstance(context, int) else None,
            )
        )
    return entries


class OpenRouterAdapter(Adapter):
    diff_mode = "catalog"
    source_id = "openrouter:api"
    adapter = "openrouter"
    provider = "openrouter"
    url = CATALOG_URL

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or None

    def fetch(self, ctx: FetchContext) -> CollectResult:
        headers = etag_headers(ctx, {"Accept": "application/json"})
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        entries: list[Entry] = []
        url = self.url
        etag = None
        seen_urls: set[str] = set()
        try:
            for page in range(MAX_PAGES):
                if url in seen_urls:
                    return failure(self.source_id, self.diff_mode, "catalog pagination loop", incomplete=True)
                seen_urls.add(url)
                page_headers = headers if page == 0 else {"Accept": "application/json"}
                if self.api_key:
                    page_headers["Authorization"] = f"Bearer {self.api_key}"
                response = ctx.http.get(url, page_headers)
                if page == 0 and response.status == 304:
                    return CollectResult(
                        source_id=self.source_id,
                        ok=True,
                        not_modified=True,
                        etag=response.header("ETag"),
                        diff_mode=self.diff_mode,
                    )
                if page == 0:
                    etag = response.header("ETag")
                payload = read_json(response)
                if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                    return failure(self.source_id, self.diff_mode, "catalog missing data[]")
                entries.extend(entries_from_rows(payload["data"]))
                link = payload.get("links") or {}
                nxt = link.get("next") if isinstance(link, dict) else None
                if not nxt:
                    break
                url = urljoin(url, str(nxt))
            else:
                return failure(
                    self.source_id,
                    self.diff_mode,
                    "catalog pagination truncated",
                    incomplete=True,
                )
        except RemoteError as exc:
            return failure(self.source_id, self.diff_mode, str(exc))
        if not entries:
            return CollectResult(
                source_id=self.source_id,
                ok=False,
                empty=True,
                error="empty body",
                diff_mode=self.diff_mode,
            )
        return CollectResult(
            source_id=self.source_id,
            ok=True,
            etag=etag,
            entries=entries,
            diff_mode=self.diff_mode,
        )
