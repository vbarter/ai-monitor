import json

from model_radar.adapters.base import FetchContext
from model_radar.adapters.html_changelog import HtmlChangelogAdapter, parse_html_index, parse_markdown_dated
from model_radar.adapters.rss import RssAdapter
from model_radar.config import ChangelogSpec, FeedSpec, load_config
from model_radar.db import connect
from model_radar.http import HttpClient, HttpResponse
from model_radar.scan import run_scan

DEEPSEEK_NEWS = """
<html><body>
<a href="/en/news/deepseek-v4-1-flash/">
  <h2>Introducing DeepSeek-V4.1-Flash: smarter, faster, more efficient.</h2>
</a>
<a href="/en/news/v3-2/"><h3>DeepSeek-V3.2: Pushing the Frontier</h3></a>
<a href="/en/download/">Downloads</a>
</body></html>
"""

KIMI = """
<html><body>
<a href="/blog/kimi-k3-1-pro" aria-label="Kimi K3.1 Pro"></a>
<a href="/blog/perception-bench" aria-label="PerceptionBench"></a>
<a href="https://www.kimi.ai/zh-hans/blog/">中文</a>
<a href="/blog/kimi-k3-1-pro">Kimi K3.1 Pro tech blog</a>
</body></html>
"""

MINIMAX = """
<html><body>
<a href="/blog/minimax-m31f"><span>MiniMax M3.1 Flash</span><span>NEW</span></a>
<a href="/news/minimax-speech-28"><span>MiniMax Speech 2.8</span></a>
<a href="/blog">Research</a>
<a href="/news">News</a>
</body></html>
"""

ANTHROPIC = """
<html><body>
<a href="/claude-fable-and-mythos-5-2"><h2>Introducing Claude Fable 5.2 and Claude Mythos 5.2</h2></a>
<a href="/news/announcements">Announcements</a>
<a href="/news/s1-sec"><h3>Anthropic files S-1 with the SEC</h3></a>
</body></html>
"""

META = """
<html><body>
<a href="https://ai.meta.com/blog/introducing-muse-spark/"><div>FEATURED</div><h4>Introducing Muse Spark</h4></a>
<a href="https://ai.meta.com/blog/introducing-muse-spark/">Learn More</a>
<a href="https://ai.meta.com/blog/some-lab-note/"><h4>How a lab note uses models</h4></a>
</body></html>
"""

MARKDOWN = """
# Change Log

## Date: 2026-11-13

### Releasing the full suite of application APIs

text

## Date: 2026-10-17

### DeepSeek-V4.1-Flash-1017 Update
"""

ZAI_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <item>
      <title><![CDATA[2026-08-26]]></title>
      <link>https://docs.z.ai/release-notes/new-released#2026-08-26</link>
      <guid>5a241cead74dd8e0</guid>
      <content:encoded><![CDATA[<p>Learn more in our <a href="https://docs.z.ai/guides/vlm/glm-5.3-flash">documentation</a>.</p>]]></content:encoded>
    </item>
  </channel>
</rss>
"""


def test_profiles_keep_release_cards_and_drop_nav():
    news = {row["model_id"]: row["title"] for row in parse_html_index(DEEPSEEK_NEWS, "https://www.deepseek.com/en/news/", "deepseek_news")}
    assert news == {
        "https://www.deepseek.com/en/news/deepseek-v4-1-flash": "Introducing DeepSeek-V4.1-Flash: smarter, faster, more efficient.",
        "https://www.deepseek.com/en/news/v3-2": "DeepSeek-V3.2: Pushing the Frontier",
    }
    kimi = parse_html_index(KIMI, "https://www.kimi.ai/blog/", "kimi_blog")
    by_id = {row["model_id"]: row["title"] for row in kimi}
    assert set(by_id) == {
        "https://www.kimi.ai/blog/kimi-k3-1-pro",
        "https://www.kimi.ai/blog/perception-bench",
    }
    assert by_id["https://www.kimi.ai/blog/kimi-k3-1-pro"] == "Kimi K3.1 Pro"
    mini = {row["title"] for row in parse_html_index(MINIMAX, "https://www.minimax.io/news", "minimax_news")}
    assert mini == {"MiniMax M3.1 Flash", "MiniMax Speech 2.8"}
    anth = {row["model_id"]: row["title"] for row in parse_html_index(ANTHROPIC, "https://www.anthropic.com/news", "anthropic_news")}
    assert "https://www.anthropic.com/claude-fable-and-mythos-5-2" in anth
    assert "https://www.anthropic.com/news/s1-sec" in anth
    assert all("/announcements" not in key for key in anth)
    meta = {row["model_id"]: row["title"] for row in parse_html_index(META, "https://ai.meta.com/blog/", "meta_blog")}
    assert meta["https://ai.meta.com/blog/introducing-muse-spark"] == "Introducing Muse Spark"


def test_markdown_changelog_splits_dated_sections():
    rows = parse_markdown_dated(MARKDOWN, "https://api-docs.deepseek.com/updates.md")
    titles = [row["title"] for row in rows]
    assert titles == ["Releasing the full suite of application APIs", "DeepSeek-V4.1-Flash-1017 Update"]
    assert rows[0]["model_id"].startswith("https://api-docs.deepseek.com/updates.md#2026-11-13-")
    assert rows[0]["url"] == "https://api-docs.deepseek.com/updates"


def test_zai_rss_uses_model_slug_and_is_official():
    feed = FeedSpec(
        id="zai-releases",
        provider="zhipu",
        url="https://docs.z.ai/release-notes/new-released/rss.xml",
        filter=False,
        kind="official_release",
        first_party=True,
    )

    def transport(method, url, headers, body, timeout):
        return HttpResponse(200, ZAI_RSS.encode(), {}, url)

    http = HttpClient(transport=transport, sleeper=lambda _s: None, attempts=1, timeout=1)
    result = RssAdapter(feed).collect(FetchContext(http))
    assert result.ok
    entry = result.entries[0]
    assert entry.title == "glm-5.3-flash (2026-08-26)"
    assert entry.model_id == "https://docs.z.ai/release-notes/new-released#2026-08-26"
    assert entry.kind == "official_release"
    assert entry.first_party is True


def test_kimi_page_classifies_model_post_and_research_note():
    page = ChangelogSpec(
        id="kimi-blog",
        provider="moonshot",
        url="https://www.kimi.ai/blog/",
        enabled=True,
        profile="kimi_blog",
        filter="official_page",
    )

    def transport(method, url, headers, body, timeout):
        return HttpResponse(200, KIMI.encode(), {}, url)

    http = HttpClient(transport=transport, sleeper=lambda _s: None, attempts=1, timeout=1)
    result = HtmlChangelogAdapter(page).collect(FetchContext(http))
    kinds = {entry.title: entry.kind for entry in result.entries}
    assert kinds["Kimi K3.1 Pro"] == "official_release"
    assert kinds["PerceptionBench"] == "changelog_note"


def test_page_scan_baselines_then_alerts_and_keeps_snapshot_on_error(tmp_path):
    db_path = tmp_path / "radar.sqlite"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
huggingface:
  enabled: false
providers: {{}}
rss: []
changelogs:
  - id: deepseek-news
    provider: deepseek
    url: https://www.deepseek.com/en/news/
    enabled: true
    profile: deepseek_news
    filter: false
database: "{db_path.as_posix()}"
""",
        encoding="utf-8",
    )
    config = load_config(config_path)
    url = "https://www.deepseek.com/en/news/"
    second = DEEPSEEK_NEWS.replace(
        "</body>",
        '<a href="/en/news/v4-pro/"><h2>DeepSeek-V4-Pro</h2></a></body>',
    )
    bodies = [DEEPSEEK_NEWS.encode(), b"", second.encode()]

    class Script:
        def __init__(self):
            self.posts = []
            self.n = 0

        def __call__(self, method, url, headers, body, timeout):
            if method == "POST":
                self.posts.append(body)
                return HttpResponse(200, b"ok", {}, url)
            payload = bodies[min(self.n, len(bodies) - 1)]
            status = 500 if self.n == 1 else 200
            self.n += 1
            return HttpResponse(status, payload, {}, url)

    script = Script()
    http = HttpClient(transport=script, sleeper=lambda _s: None, attempts=1, timeout=1)
    env = {"SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/T/B/X"}
    db = connect(config.database)
    assert run_scan(config, db, http, env=env).alerts_sent == 0
    snap = db.get_snapshot("page:deepseek-news")
    assert len(snap["body"]["entries"]) == 2
    assert run_scan(config, db, http, env=env).failed == 1
    assert len(db.get_snapshot("page:deepseek-news")["body"]["entries"]) == 2
    stats = run_scan(config, db, http, env=env)
    assert stats.new_releases == 1
    assert stats.alerts_sent == 1
    text = json.loads(script.posts[0])["text"]
    assert "DeepSeek-V4-Pro" in text
    assert "v4-1-flash" not in text
