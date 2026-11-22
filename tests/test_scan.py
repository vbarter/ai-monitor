import json
from pathlib import Path

from model_radar.adapters.huggingface import HuggingFaceOrgAdapter
from model_radar.config import load_config
from model_radar.db import connect
from model_radar.http import HttpClient, HttpResponse
from model_radar.scan import run_scan

ROOT = Path(__file__).resolve().parents[1]


class Script:
    def __init__(self, post_status=200):
        self.queues: dict[str, list] = {}
        self.posts: list[bytes] = []
        self.post_status = post_status

    def queue(self, url, status, body, headers=None):
        if isinstance(body, str):
            body = body.encode()
        elif isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        self.queues.setdefault(url, []).append((status, body, headers or {}))
        return self

    def __call__(self, method, url, headers, body, timeout):
        if method == "POST":
            self.posts.append(body or b"")
            payload = b"ok" if self.post_status < 300 else b"no"
            return HttpResponse(self.post_status, payload, {}, url)
        items = self.queues.get(url)
        if not items:
            return HttpResponse(404, f"no route {url}".encode(), {}, url)
        if len(items) > 1:
            status, payload, hdrs = items.pop(0)
        else:
            status, payload, hdrs = items[0]
        return HttpResponse(status, payload, hdrs, url)

    def texts(self):
        return [json.loads(post)["text"] for post in self.posts]


def http(script: Script) -> HttpClient:
    return HttpClient(transport=script, sleeper=lambda _s: None, attempts=1, timeout=1)


def write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_quiet_baseline_then_weights_drop_excludes_quant(tmp_path):
    path = write_config(
        tmp_path,
        """
timezone: Asia/Shanghai
huggingface:
  enabled: true
  authors: [Qwen]
  per_author_limit: 40
providers: {{}}
rss: []
alerts:
  slack_webhook_env: SLACK_WEBHOOK_URL
  slack_channel: "#自媒体"
  max_immediate_per_hour: 8
database: "{db}"
""".format(db=(tmp_path / "radar.sqlite").as_posix()),
    )
    config = load_config(path)
    adapter = HuggingFaceOrgAdapter("Qwen", limit=40)
    first = [
        {"id": "Qwen/Qwen3-8B", "createdAt": "2026-11-20T01:00:00.000Z", "private": False, "tags": ["safetensors"]},
        {"id": "Qwen/Qwen3-8B-GGUF", "createdAt": "2026-11-20T02:00:00.000Z", "private": False, "tags": ["gguf"]},
    ]
    second = first + [
        {"id": "Qwen/Qwen3-32B", "createdAt": "2026-11-22T01:00:00.000Z", "private": False, "tags": ["text-generation"]},
        {"id": "Qwen/Qwen3-32B-lora", "createdAt": "2026-11-22T02:00:00.000Z", "private": False, "tags": ["lora"]},
    ]
    script = Script()
    script.queue(adapter.url, 200, first)
    script.queue(adapter.url, 200, second)
    script.queue(adapter.url, 200, second)
    env = {"SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/T00/B00/secret"}
    db = connect(config.database)

    first_stats = run_scan(config, db, http(script), env=env)
    assert first_stats.alerts_sent == 0
    assert script.posts == []
    rows = db.conn.execute("SELECT model_id, kind, notified FROM seen_items ORDER BY model_id").fetchall()
    assert [tuple(row) for row in rows] == [
        ("Qwen/Qwen3-8B", "weights_drop", "baseline"),
        ("Qwen/Qwen3-8B-GGUF", "noise", "suppressed"),
    ]

    second_stats = run_scan(config, db, http(script), env=env)
    assert second_stats.new_releases == 1
    assert second_stats.alerts_sent == 1
    assert len(script.posts) == 1
    text = script.texts()[0]
    assert "Qwen/Qwen3-32B" in text
    assert "Qwen3-32B-lora" not in text
    assert "weights_drop" in text
    assert "Asia/Shanghai" in text
    stored = db.conn.execute("SELECT body_json FROM alerts").fetchone()[0]
    assert "secret" not in stored
    assert "hooks.slack.com" not in stored

    third = run_scan(config, db, http(script), env=env)
    assert third.alerts_sent == 0
    assert len(script.posts) == 1


def test_empty_or_error_does_not_replace_snapshot_and_isolates_failure(tmp_path):
    db_path = tmp_path / "radar.sqlite"
    path = write_config(
        tmp_path,
        f"""
huggingface:
  enabled: false
providers:
  openrouter:
    env: OPENROUTER_API_KEY
    optional: true
rss:
  - {{id: lab, provider: google, url: "https://example.test/blog.xml", filter: release_keywords}}
alerts:
  slack_webhook_env: SLACK_WEBHOOK_URL
  router_catalog_immediate: false
database: "{db_path.as_posix()}"
""",
    )
    config = load_config(path)
    catalog = "https://openrouter.ai/api/v1/models"
    feed = "https://example.test/blog.xml"
    good = {
        "data": [
            {"id": "example/a", "name": "A", "canonical_slug": "example/a", "pricing": {"prompt": "1", "completion": "2"}},
            {"id": "example/b", "name": "B", "canonical_slug": "example/b", "pricing": {"prompt": "1", "completion": "2"}},
        ],
        "links": {"next": None},
    }
    priced = {
        "data": [
            {"id": "example/a", "name": "A", "canonical_slug": "example/a", "pricing": {"prompt": "9", "completion": "2"}},
            {"id": "example/b", "name": "B", "canonical_slug": "example/b", "pricing": {"prompt": "1", "completion": "2"}},
            {"id": "example/c", "name": "C", "canonical_slug": "example/c", "pricing": {"prompt": "1", "completion": "2"}},
        ],
        "links": {"next": None},
    }
    blog = """<?xml version="1.0"?><rss version="2.0"><channel>
      <item><title>Introducing Gemini 4 Neon</title><link>https://example.com/g</link><guid>https://example.com/g</guid><description>model</description></item>
    </channel></rss>"""
    script = Script()
    script.queue(catalog, 200, good)
    script.queue(feed, 500, "nope")
    script.queue(catalog, 200, {"data": [], "links": {"next": None}})
    script.queue(feed, 500, "nope")
    script.queue(catalog, 200, priced)
    script.queue(feed, 200, blog)
    env = {"SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/T00/B00/secret"}
    db = connect(config.database)

    run_scan(config, db, http(script), env=env)
    snap = db.get_snapshot("openrouter:api")
    assert [row["model_id"] for row in snap["body"]["entries"]] == ["example/a", "example/b"]
    assert db.get_snapshot("rss:lab") is None
    assert script.posts == []

    run_scan(config, db, http(script), env=env)
    snap = db.get_snapshot("openrouter:api")
    assert [row["model_id"] for row in snap["body"]["entries"]] == ["example/a", "example/b"]
    assert db.get_source("openrouter:api")["consecutive_failures"] == 1
    assert script.posts == []

    stats = run_scan(config, db, http(script), env=env)
    snap = db.get_snapshot("openrouter:api")
    assert [row["model_id"] for row in snap["body"]["entries"]] == ["example/a", "example/b", "example/c"]
    # Router adds are not first-party, so example/c does not page.
    assert all("example/c" not in text for text in script.texts())
    # The blog post is an official_release and does page. The lab source's first
    # success is a quiet baseline, so this scan still does not page for it.
    assert stats.failed == 0
    assert db.get_source("rss:lab")["last_ok_at"]
    noted = db.conn.execute(
        "SELECT notified, kind FROM seen_items WHERE model_id = ?",
        ("https://example.com/g",),
    ).fetchone()
    assert tuple(noted) == ("baseline", "official_release")
    updates = db.conn.execute("SELECT payload_json FROM events WHERE payload_json LIKE '%catalog_update%'").fetchall()
    assert updates


def test_first_party_catalog_add_pages_and_collapses(tmp_path):
    db_path = tmp_path / "radar.sqlite"
    path = write_config(
        tmp_path,
        f"""
huggingface:
  enabled: false
providers:
  openai:
    env: OPENAI_API_KEY
rss: []
alerts:
  max_immediate_per_hour: 2
  slack_webhook_env: SLACK_WEBHOOK_URL
database: "{db_path.as_posix()}"
""",
    )
    config = load_config(path)
    url = "https://api.openai.com/v1/models"
    script = Script()
    script.queue(url, 200, {"data": [{"id": "gpt-old"}]})
    script.queue(
        url,
        200,
        {"data": [{"id": "gpt-old"}, {"id": "gpt-a"}, {"id": "gpt-b"}, {"id": "gpt-c"}, {"id": "ft:gpt-a:acme:x:1"}]},
    )
    env = {"OPENAI_API_KEY": "sk-test", "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/T/B/X"}
    db = connect(config.database)
    assert run_scan(config, db, http(script), env=env).alerts_sent == 0
    stats = run_scan(config, db, http(script), env=env)
    assert stats.new_releases == 3
    assert stats.alerts_sent == 1
    assert len(script.posts) == 1
    text = script.texts()[0]
    assert "gpt-a" in text and "gpt-b" in text and "gpt-c" in text
    assert "ft:gpt-a" not in text
    assert "已合并" in text
    ft = db.conn.execute("SELECT kind, notified FROM seen_items WHERE model_id LIKE 'ft:%'").fetchone()
    assert tuple(ft) == ("noise", "suppressed")


def test_slack_failure_is_retried_next_scan(tmp_path):
    db_path = tmp_path / "radar.sqlite"
    path = write_config(
        tmp_path,
        f"""
huggingface:
  enabled: false
providers:
  deepseek:
    env: DEEPSEEK_API_KEY
rss: []
alerts:
  slack_webhook_env: SLACK_WEBHOOK_URL
database: "{db_path.as_posix()}"
""",
    )
    config = load_config(path)
    url = "https://api.deepseek.com/models"
    failing = Script(post_status=500)
    failing.queue(url, 200, {"data": [{"id": "deepseek-chat"}]})
    failing.queue(url, 200, {"data": [{"id": "deepseek-chat"}, {"id": "deepseek-v4"}]})
    env = {"DEEPSEEK_API_KEY": "dk", "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/T/B/X"}
    db = connect(config.database)
    run_scan(config, db, http(failing), env=env)
    stats = run_scan(config, db, http(failing), env=env)
    assert stats.new_releases == 1
    assert stats.alerts_sent == 0
    row = db.conn.execute("SELECT notified FROM seen_items WHERE model_id = 'deepseek-v4'").fetchone()
    assert row["notified"] is None
    alert = db.conn.execute("SELECT status, error FROM alerts").fetchone()
    assert alert["status"] == "failed"
    assert "dk" not in (alert["error"] or "")

    ok = Script()
    ok.queue(url, 200, {"data": [{"id": "deepseek-chat"}, {"id": "deepseek-v4"}]})
    stats = run_scan(config, db, http(ok), env=env)
    assert stats.new_releases == 0
    assert stats.alerts_sent == 1
    row = db.conn.execute("SELECT notified FROM seen_items WHERE model_id = 'deepseek-v4'").fetchone()
    assert row["notified"] == "immediate"
    assert db.conn.execute("SELECT status FROM alerts").fetchone()["status"] == "sent"


def test_health_warning_once_after_three_failures(tmp_path):
    db_path = tmp_path / "radar.sqlite"
    path = write_config(
        tmp_path,
        f"""
huggingface:
  enabled: false
providers: {{}}
rss:
  - {{id: lab, provider: openai, url: "https://example.test/news.xml", filter: release_keywords}}
alerts:
  slack_webhook_env: SLACK_WEBHOOK_URL
database: "{db_path.as_posix()}"
""",
    )
    config = load_config(path)
    url = "https://example.test/news.xml"
    script = Script()
    for _ in range(4):
        script.queue(url, 503, "down")
    env = {"SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/T/B/X"}
    db = connect(config.database)
    assert run_scan(config, db, http(script), env=env).alerts_sent == 0
    assert run_scan(config, db, http(script), env=env).alerts_sent == 0
    third = run_scan(config, db, http(script), env=env)
    assert third.alerts_sent == 1
    assert "连续失败 3 次" in script.texts()[0]
    fourth = run_scan(config, db, http(script), env=env)
    assert fourth.alerts_sent == 0
    assert len(script.posts) == 1


def test_upgrade_from_catalog_add_to_official_release_pages_once(tmp_path):
    db_path = tmp_path / "radar.sqlite"
    path = write_config(
        tmp_path,
        f"""
huggingface:
  enabled: false
providers:
  openrouter:
    env: OPENROUTER_API_KEY
    optional: true
rss:
  - {{id: openrouter-rss, provider: openrouter, url: "https://openrouter.ai/api/v1/models?use_rss=true", filter: release_keywords, first_party: true, id_mode: openrouter}}
alerts:
  router_catalog_immediate: true
  slack_webhook_env: SLACK_WEBHOOK_URL
database: "{db_path.as_posix()}"
""",
    )
    config = load_config(path)
    catalog = "https://openrouter.ai/api/v1/models"
    feed = "https://openrouter.ai/api/v1/models?use_rss=true"
    base = {
        "data": [{"id": "openai/gpt-x", "name": "GPT X", "canonical_slug": "openai/gpt-x-20260101"}],
        "links": {"next": None},
    }
    xml = """<?xml version="1.0"?><rss version="2.0"><channel><item>
      <title>Introducing GPT X</title>
      <link>https://openrouter.ai/openai/gpt-x</link>
      <guid>openai/gpt-x-20260101</guid>
      <description>model release</description>
    </item></channel></rss>"""
    script = Script()
    script.queue(catalog, 200, base)
    script.queue(feed, 404, "missing")
    script.queue(catalog, 200, base)
    script.queue(feed, 200, xml)
    script.queue(catalog, 200, base)
    script.queue(feed, 200, xml)
    env = {"SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/T/B/X"}
    db = connect(config.database)
    # First catalog sight is the quiet baseline, so force the row into the
    # "already alerted" state the upgrade rule cares about.
    run_scan(config, db, http(script), env=env)
    db.conn.execute(
        "UPDATE seen_items SET notified = 'immediate' WHERE model_id = 'openai/gpt-x'"
    )
    db.commit()
    assert run_scan(config, db, http(script), env=env).alerts_sent == 1
    text = script.texts()[0]
    assert "official_release" in text
    assert "openai/gpt-x" in text
    again = run_scan(config, db, http(script), env=env)
    assert again.alerts_sent == 0
    assert len(script.posts) == 1


def test_second_blog_post_alerts_as_official_release(tmp_path):
    db_path = tmp_path / "radar.sqlite"
    path = write_config(
        tmp_path,
        f"""
huggingface:
  enabled: false
providers: {{}}
rss:
  - {{id: lab, provider: google, url: "https://example.test/blog.xml", filter: release_keywords}}
alerts:
  slack_webhook_env: SLACK_WEBHOOK_URL
database: "{db_path.as_posix()}"
""",
    )
    config = load_config(path)
    url = "https://example.test/blog.xml"
    first = """<?xml version="1.0"?><rss version="2.0"><channel>
      <item><title>Introducing Gemini 4 Neon</title><link>https://example.com/g</link><guid>https://example.com/g</guid><description>model</description></item>
    </channel></rss>"""
    second = """<?xml version="1.0"?><rss version="2.0"><channel>
      <item><title>Introducing Gemini 4 Neon</title><link>https://example.com/g</link><guid>https://example.com/g</guid><description>model</description></item>
      <item><title>Announcing Llama 5 weights</title><link>https://example.com/l</link><guid>https://example.com/l</guid><description>open weights</description></item>
    </channel></rss>"""
    script = Script()
    script.queue(url, 200, first)
    script.queue(url, 200, second)
    env = {"SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/T/B/X"}
    db = connect(config.database)
    assert run_scan(config, db, http(script), env=env).alerts_sent == 0
    stats = run_scan(config, db, http(script), env=env)
    assert stats.new_releases == 1
    assert stats.alerts_sent == 1
    text = script.texts()[0]
    assert "official_release" in text
    assert "https://example.com/l" in text
    assert "https://example.com/g" not in text


def test_missing_provider_key_is_skipped_without_failure(tmp_path):
    path = write_config(
        tmp_path,
        f"""
huggingface:
  enabled: false
providers:
  openai:
    env: OPENAI_API_KEY
  ark:
    env: ARK_API_KEY
    enabled: true
rss: []
database: "{(tmp_path / 'radar.sqlite').as_posix()}"
""",
    )
    config = load_config(path)
    script = Script()
    db = connect(config.database)
    stats = run_scan(config, db, http(script), env={})
    assert stats.sources == 0
    assert stats.skipped == 2
    assert stats.failed == 0
    assert db.conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0
