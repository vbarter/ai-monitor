"""DeepSeek list models: GET https://api.deepseek.com/models"""

from __future__ import annotations

from model_radar.adapters.base import Adapter, CollectResult, FetchContext, failure
from model_radar.adapters.openai_compat import entries_from_model_list, parse_model_list
from model_radar.http import RemoteError, read_json

URL = "https://api.deepseek.com/models"


class DeepSeekAdapter(Adapter):
    source_id = "deepseek:api"
    adapter = "deepseek"
    provider = "deepseek"
    url = URL
    diff_mode = "catalog"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def fetch(self, ctx: FetchContext) -> CollectResult:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }
        response = ctx.http.get(self.url, headers)
        try:
            payload = read_json(response)
            rows = parse_model_list(payload)
        except RemoteError as exc:
            return failure(self.source_id, self.diff_mode, str(exc))
        entries = entries_from_model_list(
            rows,
            provider="deepseek",
            url_for=lambda model_id: "https://api-docs.deepseek.com/api/list-models/",
        )
        if not entries:
            return CollectResult(
                source_id=self.source_id, ok=False, empty=True, error="empty body", diff_mode=self.diff_mode
            )
        return CollectResult(
            source_id=self.source_id,
            ok=True,
            etag=response.header("ETag"),
            entries=entries,
            diff_mode=self.diff_mode,
        )
