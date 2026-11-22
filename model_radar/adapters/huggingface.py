"""Allowlisted Hugging Face orgs via the Hub REST API.

Uses `sort=createdAt`. `sort=created` (no At) returns HTTP 400 on this API.
"""

from __future__ import annotations

from urllib.parse import urlencode

from model_radar.adapters.base import Adapter, CollectResult, Entry, FetchContext, etag_headers, failure
from model_radar.http import RemoteError, read_json

HUB = "https://huggingface.co/api/models"

HF_PROVIDER = {
    "Qwen": "alibaba",
    "deepseek-ai": "deepseek",
    "ByteDance-Seed": "bytedance",
    "XiaomiMiMo": "xiaomi",
    "moonshotai": "moonshot",
    "zai-org": "zhipu",
    "MiniMaxAI": "minimax",
    "tencent": "tencent",
    "baidu": "baidu",
    "meta-llama": "meta",
    "google": "google",
    "openai": "openai",
    "facebook": "meta",
    "microsoft": "microsoft",
}


def provider_for(author: str) -> str:
    return HF_PROVIDER.get(author, author.lower())


class HuggingFaceOrgAdapter(Adapter):
    diff_mode = "cursor"

    def __init__(self, author: str, *, limit: int = 40, token: str | None = None):
        self.author = author
        self.limit = limit
        self.token = token or None
        self.source_id = f"hf:{author}"
        self.adapter = "huggingface"
        self.provider = provider_for(author)
        query = urlencode(
            {
                "author": author,
                "sort": "createdAt",
                "direction": "-1",
                "limit": str(limit),
            }
        )
        self.url = f"{HUB}?{query}"

    def fetch(self, ctx: FetchContext) -> CollectResult:
        headers = etag_headers(ctx, {"Accept": "application/json"})
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        response = ctx.http.get(self.url, headers)
        if response.status == 304:
            return CollectResult(
                source_id=self.source_id,
                ok=True,
                not_modified=True,
                etag=response.header("ETag"),
                diff_mode=self.diff_mode,
            )
        try:
            payload = read_json(response)
        except RemoteError as exc:
            return failure(self.source_id, self.diff_mode, str(exc))
        if not isinstance(payload, list):
            return failure(self.source_id, self.diff_mode, "hub response was not a list")
        entries: list[Entry] = []
        for row in payload:
            if not isinstance(row, dict):
                continue
            if row.get("private"):
                continue
            model_id = str(row.get("id") or row.get("modelId") or "").strip()
            if not model_id:
                continue
            tags = row.get("tags") or []
            tag_text = " ".join(str(tag) for tag in tags) if isinstance(tags, list) else ""
            pipeline = str(row.get("pipeline_tag") or "")
            entries.append(
                Entry(
                    provider=self.provider,
                    model_id=model_id,
                    title=model_id,
                    official_url=f"https://huggingface.co/{model_id}",
                    kind="weights_drop",
                    first_party=True,
                    published_at=str(row.get("createdAt") or "") or None,
                    haystack=f"{tag_text} {pipeline}".strip(),
                )
            )
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
            etag=response.header("ETag"),
            entries=entries,
            diff_mode=self.diff_mode,
        )
