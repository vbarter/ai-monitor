"""Shared adapter records.

`diff_mode` tells the integrator how to treat a successful body:

- catalog: full id list. Missing from the new body is not a deletion alert.
  An empty or error body must not replace the stored snapshot.
- cursor: page of newest rows. Alert only when `published_at` is past the cursor.
- feed: item list. Unseen ids are new; an empty feed does not clear history.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from model_radar.http import HttpClient
from model_radar.redact import clean_error


@dataclass
class Entry:
    provider: str
    model_id: str
    version: str = ""
    title: str = ""
    official_url: str = ""
    kind: str = "catalog_add"
    first_party: bool = False
    published_at: str | None = None
    pricing: dict | None = None
    context_length: int | None = None
    haystack: str = ""


@dataclass
class CollectResult:
    source_id: str
    ok: bool
    empty: bool = False
    not_modified: bool = False
    error: str | None = None
    etag: str | None = None
    entries: list[Entry] = field(default_factory=list)
    diff_mode: str = "catalog"
    incomplete: bool = False


@dataclass
class FetchContext:
    http: HttpClient
    previous_etag: str | None = None


class Adapter:
    source_id: str
    adapter: str
    provider: str
    url: str
    diff_mode: str = "catalog"

    def collect(self, ctx: FetchContext) -> CollectResult:
        try:
            return self.fetch(ctx)
        except Exception as exc:
            return CollectResult(
                source_id=self.source_id,
                ok=False,
                error=clean_error(f"{type(exc).__name__}: {exc}"),
                diff_mode=self.diff_mode,
            )

    def fetch(self, ctx: FetchContext) -> CollectResult:
        raise NotImplementedError


def etag_headers(ctx: FetchContext, extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = dict(extra or {})
    if ctx.previous_etag:
        headers["If-None-Match"] = ctx.previous_etag
    return headers


def failure(source_id: str, diff_mode: str, message: str, *, incomplete: bool = False) -> CollectResult:
    return CollectResult(
        source_id=source_id,
        ok=False,
        error=clean_error(message),
        diff_mode=diff_mode,
        incomplete=incomplete,
    )
