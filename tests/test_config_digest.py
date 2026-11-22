from pathlib import Path

from model_radar.config import load_config
from model_radar.db import connect
from model_radar.digest import run_digest
from model_radar.http import HttpClient, HttpResponse

ROOT = Path(__file__).resolve().parents[1]


def test_shipped_config_has_no_secrets_and_mvp_flags():
    text = (ROOT / "config.yaml").read_text(encoding="utf-8")
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "hooks.slack.com" not in text
    assert "sk-" not in text
    assert "hooks.slack.com" not in env
    config = load_config(ROOT / "config.yaml")
    assert config.timezone == "Asia/Shanghai"
    assert "Qwen" in config.huggingface.authors
    assert config.huggingface.per_author_limit == 40
    assert config.providers["openrouter"].optional is True
    assert config.providers["openrouter"].enabled is True
    assert config.providers["openai"].enabled is False
    assert config.providers["anthropic"].enabled is False
    assert config.providers["gemini"].enabled is False
    assert config.providers["deepseek"].enabled is False
    assert config.providers["ark"].enabled is False
    assert config.providers["ark"].implemented is False
    assert config.alerts.router_catalog_immediate is False
    assert config.alerts.slack_channel == "#自媒体"
    feed = {item.id: item for item in config.rss}
    assert feed["openrouter-rss"].filter is False
    assert feed["openrouter-rss"].id_mode == "openrouter"
    assert feed["hf-blog"].url == "https://huggingface.co/blog/feed.xml"
    assert feed["qwen-legacy-blog"].url == "https://qwenlm.github.io/blog/index.xml"
    assert feed["openai-news"].url == "https://openai.com/news/rss.xml"
    assert feed["zai-releases"].url == "https://docs.z.ai/release-notes/new-released/rss.xml"
    assert feed["zai-releases"].filter is False
    assert feed["deepmind"].url == "https://deepmind.google/blog/rss.xml"
    assert "deepmind.google/discover/blog/rss.xml" not in text
    pages = {page.id: page for page in config.changelogs}
    assert pages["deepseek-news"].enabled is True
    assert pages["deepseek-updates"].url == "https://api-docs.deepseek.com/updates.md"
    assert pages["kimi-blog"].enabled is True
    assert pages["minimax-news"].enabled is True
    assert pages["anthropic-news"].enabled is True
    assert pages["meta-blog"].enabled is True
    assert pages["mimo-blog"].enabled is False
    assert pages["mimo-blog"].url == "https://mimo.xiaomi.com/blog"
    config.exclude_regexes()


def test_digest_is_silent_when_empty_and_send_marks_rows(tmp_path, capsys):
    config = load_config(ROOT / "config.yaml")
    config.database = str(tmp_path / "radar.sqlite")
    db = connect(config.database)
    calls = {}

    def transport(method, url, headers, body, timeout):
        calls["url"] = url
        calls["body"] = body
        return HttpResponse(200, b"ok", {}, url)

    http = HttpClient(transport=transport, sleeper=lambda _s: None, attempts=1, timeout=1)
    assert run_digest(config, db, http, send=True, env={"SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/T/B/X"}) == 0
    assert "url" not in calls
    assert capsys.readouterr().out == ""

    db.ensure_source("rss:lab", "rss", "google", "https://example.test/x")
    db.insert_seen(
        {
            "fingerprint": "abc",
            "canonical_id": "google|https://example.test/post|",
            "provider": "google",
            "model_id": "https://example.test/post",
            "version": "",
            "kind": "changelog_note",
            "title": "Robotics research notes",
            "official_url": "https://example.test/post",
            "first_seen_at": "2026-11-22T00:00:00Z",
            "last_seen_at": "2026-11-22T00:00:00Z",
            "first_source": "rss:lab",
            "notified": None,
            "raw_hash": "h",
            "first_party": 0,
            "upgraded": 0,
        }
    )
    db.commit()
    assert run_digest(config, db, http, send=False) == 0
    printed = capsys.readouterr().out
    assert "Robotics research notes" in printed
    assert "url" not in calls
    assert db.get_seen("abc")["notified"] is None

    assert run_digest(config, db, http, send=True, env={"SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/T/B/X"}) == 0
    assert calls["url"].startswith("https://")
    assert b"hooks.slack.com" not in db.conn.execute("SELECT body_json FROM alerts").fetchone()[0].encode()
    assert db.get_seen("abc")["notified"] == "digest"
    # A second send has nothing new and does not post again.
    calls.clear()
    assert run_digest(config, db, http, send=True, env={"SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/T/B/X"}) == 0
    assert calls == {}
