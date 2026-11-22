import json
from pathlib import Path

from model_radar.adapters.anthropic import URL as ANTHROPIC_URL
from model_radar.adapters.anthropic import AnthropicAdapter
from model_radar.adapters.base import FetchContext
from model_radar.adapters.deepseek import URL as DEEPSEEK_URL
from model_radar.adapters.deepseek import DeepSeekAdapter
from model_radar.adapters.gemini import URL as GEMINI_URL
from model_radar.adapters.gemini import GeminiAdapter
from model_radar.adapters.huggingface import HuggingFaceOrgAdapter
from model_radar.adapters.openai import URL as OPENAI_URL
from model_radar.adapters.openai import OpenAIAdapter
from model_radar.adapters.openrouter import CATALOG_URL
from model_radar.adapters.openrouter import OpenRouterAdapter
from model_radar.adapters.rss import RssAdapter, parse_feed
from model_radar.config import FeedSpec
from model_radar.http import HttpClient, HttpResponse

FIXTURES = Path(__file__).parent / "fixtures"


def client_for(routes: dict[str, tuple[int, bytes, dict]]):
    def transport(method, url, headers, body, timeout):
        status, payload, hdrs = routes[url]
        return HttpResponse(status, payload, hdrs, url)

    return HttpClient(transport=transport, sleeper=lambda _s: None, attempts=1, timeout=1)


def test_hf_uses_created_at_and_drops_private_and_keeps_ids():
    adapter = HuggingFaceOrgAdapter("Qwen", limit=40)
    assert "sort=createdAt" in adapter.url
    assert "sort=created&" not in adapter.url
    assert "direction=-1" in adapter.url
    body = (FIXTURES / "hf_qwen.json").read_bytes()
    http = client_for({adapter.url: (200, body, {"ETag": "w/1"})})
    result = adapter.collect(FetchContext(http))
    assert result.ok
    assert result.diff_mode == "cursor"
    ids = [entry.model_id for entry in result.entries]
    assert ids == ["Qwen/Qwen3-8B", "Qwen/Qwen3-8B-GGUF"]
    assert result.entries[0].provider == "alibaba"
    assert result.entries[0].kind == "weights_drop"
    assert result.entries[0].official_url == "https://huggingface.co/Qwen/Qwen3-8B"


def test_openrouter_follows_next_and_versions_slug():
    page2 = "https://openrouter.ai/api/v1/models?offset=1"
    first = {
        "data": [
            {
                "id": "example/model-a",
                "canonical_slug": "example/model-a-20260101",
                "name": "Model A",
                "context_length": 8192,
                "pricing": {"prompt": "0.1", "completion": "0.2"},
            }
        ],
        "links": {"next": page2},
    }
    second = {
        "data": [{"id": "example/model-b", "name": "Model B", "canonical_slug": "example/model-b"}],
        "links": {"next": None},
    }
    http = client_for(
        {
            CATALOG_URL: (200, json.dumps(first).encode(), {}),
            page2: (200, json.dumps(second).encode(), {}),
        }
    )
    result = OpenRouterAdapter().collect(FetchContext(http))
    assert result.ok
    assert [(e.model_id, e.version, e.first_party) for e in result.entries] == [
        ("example/model-a", "example/model-a-20260101", False),
        ("example/model-b", "", False),
    ]
    assert result.entries[0].pricing == {"prompt": "0.1", "completion": "0.2"}


def test_openrouter_truncated_catalog_is_not_ok(monkeypatch):
    monkeypatch.setattr("model_radar.adapters.openrouter.MAX_PAGES", 1)
    payload = {"data": [{"id": "example/model-a", "name": "A"}], "links": {"next": CATALOG_URL + "?offset=1"}}
    http = client_for({CATALOG_URL: (200, json.dumps(payload).encode(), {})})
    result = OpenRouterAdapter().collect(FetchContext(http))
    assert not result.ok
    assert result.incomplete


def test_openai_shape_and_bearer_header():
    seen = {}

    def transport(method, url, headers, body, timeout):
        seen["headers"] = headers
        seen["url"] = url
        payload = {"object": "list", "data": [{"id": "gpt-4o", "owned_by": "openai"}, {"id": "ft:gpt-4o:acme:x:1"}]}
        return HttpResponse(200, json.dumps(payload).encode(), {}, url)

    http = HttpClient(transport=transport, sleeper=lambda _s: None, attempts=1, timeout=1)
    result = OpenAIAdapter("sk-test").collect(FetchContext(http))
    assert seen["url"] == OPENAI_URL
    assert seen["headers"]["Authorization"] == "Bearer sk-test"
    assert "sk-test" not in seen["url"]
    assert [entry.model_id for entry in result.entries] == ["gpt-4o", "ft:gpt-4o:acme:x:1"]
    assert result.entries[0].provider == "openai"
    assert result.entries[0].first_party


def test_deepseek_url():
    def transport(method, url, headers, body, timeout):
        assert url == DEEPSEEK_URL
        payload = {"data": [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}]}
        return HttpResponse(200, json.dumps(payload).encode(), {}, url)

    http = HttpClient(transport=transport, sleeper=lambda _s: None, attempts=1, timeout=1)
    result = DeepSeekAdapter("dk").collect(FetchContext(http))
    assert [entry.model_id for entry in result.entries] == ["deepseek-chat", "deepseek-reasoner"]


def test_anthropic_paginates_with_after_id():
    calls = []

    def transport(method, url, headers, body, timeout):
        calls.append((url, headers))
        if "after_id" not in url:
            payload = {
                "data": [{"id": "claude-a", "display_name": "Claude A", "type": "model"}],
                "has_more": True,
                "last_id": "claude-a",
            }
        else:
            payload = {"data": [{"id": "claude-b", "display_name": "Claude B"}], "has_more": False}
        return HttpResponse(200, json.dumps(payload).encode(), {}, url)

    http = HttpClient(transport=transport, sleeper=lambda _s: None, attempts=1, timeout=1)
    result = AnthropicAdapter("ak").collect(FetchContext(http))
    assert [entry.model_id for entry in result.entries] == ["claude-a", "claude-b"]
    assert calls[0][1]["x-api-key"] == "ak"
    assert calls[0][1]["anthropic-version"] == "2023-06-01"
    assert "ak" not in calls[0][0]
    assert calls[0][0].startswith(ANTHROPIC_URL)
    assert "after_id=claude-a" in calls[1][0]


def test_gemini_strips_models_prefix_and_keeps_key_off_the_url():
    calls = []

    def transport(method, url, headers, body, timeout):
        calls.append((url, headers))
        if "pageToken" not in url:
            payload = {
                "models": [{"name": "models/gemini-4-neon", "displayName": "Gemini 4 Neon", "version": "001"}],
                "nextPageToken": "n2",
            }
        else:
            payload = {"models": [{"name": "models/gemini-embed", "displayName": "Embed", "version": "001"}]}
        return HttpResponse(200, json.dumps(payload).encode(), {}, url)

    http = HttpClient(transport=transport, sleeper=lambda _s: None, attempts=1, timeout=1)
    result = GeminiAdapter("gk").collect(FetchContext(http))
    assert [(e.model_id, e.version, e.provider) for e in result.entries] == [
        ("gemini-4-neon", "001", "google"),
        ("gemini-embed", "001", "google"),
    ]
    assert calls[0][1]["x-goog-api-key"] == "gk"
    assert "gk" not in calls[0][0]
    assert calls[0][0].startswith(GEMINI_URL)


def test_rss_and_atom_parse_keywords():
    items = parse_feed((FIXTURES / "blog.xml").read_bytes())
    assert items[0]["title"] == "Introducing Gemini 4 Neon"
    assert items[0]["link"] == "https://example.com/gemini-4-neon"
    atom = parse_feed((FIXTURES / "atom.xml").read_bytes())
    assert atom[0]["title"] == "Announcing Llama 5"
    assert atom[0]["link"] == "https://example.com/llama-5"

    feed = FeedSpec(
        id="lab",
        provider="google",
        url="https://example.test/blog.xml",
        filter="release_keywords",
    )
    http = client_for({feed.url: (200, (FIXTURES / "blog.xml").read_bytes(), {})})
    result = RssAdapter(feed).collect(FetchContext(http))
    kinds = {entry.model_id: entry.kind for entry in result.entries}
    assert kinds["https://example.com/gemini-4-neon"] == "official_release"
    assert kinds["https://example.com/robotics"] == "changelog_note"


def test_openrouter_rss_identity_matches_catalog_slug():
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel>
      <item>
        <title><![CDATA[Example: Model A (example/model-a)]]></title>
        <link>https://openrouter.ai/example/model-a</link>
        <guid isPermaLink="false">example/model-a-20260101</guid>
        <description>new model</description>
      </item>
    </channel></rss>
    """
    feed = FeedSpec(
        id="openrouter-rss",
        provider="openrouter",
        url="https://openrouter.ai/api/v1/models?use_rss=true",
        filter=False,
        kind="catalog_add",
        first_party=False,
        id_mode="openrouter",
    )
    http = client_for({feed.url: (200, xml, {})})
    result = RssAdapter(feed).collect(FetchContext(http))
    entry = result.entries[0]
    assert entry.model_id == "example/model-a"
    assert entry.version == "example/model-a-20260101"
    assert entry.kind == "catalog_add"
    assert entry.first_party is False


def test_error_status_does_not_look_like_an_empty_catalog():
    http = client_for({OPENAI_URL: (503, b"", {})})
    result = OpenAIAdapter("sk").collect(FetchContext(http))
    assert not result.ok
    assert result.entries == []
    assert result.error
