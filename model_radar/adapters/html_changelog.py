"""Official public pages: one GET per hour, no login, no geo-block bypass.

Profiles match pages whose server-rendered HTML (or markdown) actually lists
posts. A page that comes back empty, as an error, or with zero extractable
items is a failure and does not replace the stored snapshot.

https://mimo.xiaomi.com/blog is intentionally not a profile. On 2026-11-22 it
was one landing page for the current flagship (an h1 plus section headings
such as "Benchmark Comparison"), not a post index.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from model_radar.adapters.base import Adapter, CollectResult, Entry, FetchContext, etag_headers, failure
from model_radar.classify import feed_decision
from model_radar.config import ChangelogSpec
from model_radar.http import RemoteError

_DATE_HEADING = re.compile(r"^## Date:\s*(\d{4}-\d{2}-\d{2})\s*$", re.M)
_SUB_HEADING = re.compile(r"^###\s+(.+?)\s*$", re.M)
_TAGS = re.compile(r"<[^>]+>")
_JUNK_TITLES = {
    "featured",
    "learn more",
    "open source",
    "research",
    "announcements",
    "features",
    "partnerships",
    "product",
    "policy",
    "safeguards",
    "security",
    "news",
    "blog",
    "all research",
    "new",
}
_ANTHROPIC_SKIP = {"announcements", "features", "partnerships", "product", "policy", "safeguards", "security"}


def strip_tags(value: str) -> str:
    text = _TAGS.sub(" ", value or "")
    return " ".join(text.split())


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:80] or "item"


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[dict] = []
        self._stack: list[dict] = []
        self._ignore = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ad = {key: value or "" for key, value in attrs}
        if tag in {"script", "style", "noscript"}:
            self._ignore += 1
            return
        if self._ignore:
            return
        if tag == "a" and ad.get("href"):
            self._stack.append(
                {
                    "href": ad["href"],
                    "aria": ad.get("aria-label") or "",
                    "parts": [],
                    "headings": [],
                    "in_heading": False,
                }
            )
            return
        if self._stack and tag in {"h1", "h2", "h3", "h4"}:
            self._stack[-1]["in_heading"] = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._ignore:
            self._ignore -= 1
            return
        if not self._stack:
            return
        if tag in {"h1", "h2", "h3", "h4"}:
            self._stack[-1]["in_heading"] = False
        elif tag == "a":
            self.anchors.append(self._stack.pop())

    def handle_data(self, data: str) -> None:
        if self._ignore or not self._stack or not data:
            return
        self._stack[-1]["parts"].append(data)
        if self._stack[-1]["in_heading"]:
            self._stack[-1]["headings"].append(data)


def _clean_title(value: str) -> str:
    text = " ".join((value or "").split())
    text = re.sub(r"(?i)\s*new$", "", text).strip()
    if text.lower().startswith("related to "):
        text = text[len("related to ") :].strip()
    text = re.sub(r"(?i)^news\s+(?:[A-Z][a-z]+ \d{1,2}, \d{4}\s*)?", "", text).strip()
    return text


def _junk(title: str) -> bool:
    return _clean_title(title).lower() in _JUNK_TITLES or len(_clean_title(title)) < 4


def _anchor_title(anchor: dict) -> str:
    heading = _clean_title(" ".join(anchor["headings"]))
    if heading and not _junk(heading):
        return heading
    aria = _clean_title(anchor["aria"])
    if aria and not _junk(aria):
        return aria
    return _clean_title(" ".join(anchor["parts"]))


def _absolute(page_url: str, href: str) -> str | None:
    if not href or href.startswith(("#", "mailto:", "javascript:")):
        return None
    joined = urljoin(page_url, href)
    parsed = urlparse(joined)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def _keep(current: str, new: str) -> str:
    if _junk(current) and not _junk(new):
        return new
    if _junk(new) and not _junk(current):
        return current
    low_current, low_new = current.lower(), new.lower()
    if low_new.startswith(low_current) or low_current.startswith(low_new):
        shorter, longer = (current, new) if len(current) <= len(new) else (new, current)
        extra = longer[len(shorter) :].strip().lower()
        if extra in {"", "tech blog"} or extra.endswith("tech blog"):
            return shorter
        return longer
    return new if len(new) > len(current) else current


def _path_of(url: str) -> str:
    path = urlparse(url).path or "/"
    if path != "/":
        path = path.rstrip("/")
    return path


def _select(url: str, profile: str) -> bool:
    path = _path_of(url)
    host = urlparse(url).netloc.lower()
    if profile == "deepseek_news":
        return bool(re.fullmatch(r"/en/news/[^/]+", path))
    if profile == "kimi_blog":
        return bool(re.fullmatch(r"/blog/[^/]+", path))
    if profile == "minimax_news":
        return bool(re.fullmatch(r"/(?:blog|news)/[^/]+", path))
    if profile == "anthropic_news":
        if re.fullmatch(r"/claude-[^/]+", path) or re.fullmatch(r"/features/[^/]+", path):
            return True
        match = re.fullmatch(r"/news/([^/]+)", path)
        return bool(match) and match.group(1).lower() not in _ANTHROPIC_SKIP
    if profile == "meta_blog":
        return host.endswith("ai.meta.com") and bool(re.fullmatch(r"/blog/[^/]+", path))
    return False


def parse_html_index(html: str, page_url: str, profile: str) -> list[dict[str, str]]:
    if profile not in {"deepseek_news", "kimi_blog", "minimax_news", "anthropic_news", "meta_blog"}:
        raise RemoteError(f"profile {profile or '(empty)'} is not a safe parser")
    parser = _AnchorParser()
    parser.feed(html or "")
    chosen: dict[str, str] = {}
    for anchor in parser.anchors:
        absolute = _absolute(page_url, anchor["href"])
        if absolute is None or not _select(absolute, profile):
            continue
        title = _anchor_title(anchor)
        if _junk(title):
            continue
        previous = chosen.get(absolute)
        chosen[absolute] = title if previous is None else _keep(previous, title)
    return [{"url": url, "title": title, "model_id": url} for url, title in chosen.items()]


def parse_markdown_dated(markdown: str, page_url: str) -> list[dict[str, str]]:
    text = (markdown or "").replace("\r\n", "\n")
    matches = list(_DATE_HEADING.finditer(text))
    if not matches:
        return []
    human = "https://api-docs.deepseek.com/updates"
    items: list[dict[str, str]] = []
    for index, match in enumerate(matches):
        date = match.group(1)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        section = text[match.end() : end]
        titles = [found.group(1).strip() for found in _SUB_HEADING.finditer(section)]
        titles = [title for title in titles if title]
        if not titles:
            titles = [f"Update {date}"]
        for title in titles:
            slug = _slug(title)
            items.append(
                {
                    "url": human,
                    "title": title,
                    "model_id": f"{page_url}#{date}-{slug}",
                    "published": date,
                }
            )
    return items


class HtmlChangelogAdapter(Adapter):
    diff_mode = "feed"
    adapter = "html_changelog"

    def __init__(self, page: ChangelogSpec):
        self.page = page
        self.source_id = f"page:{page.id}"
        self.provider = page.provider
        self.url = page.url

    def fetch(self, ctx: FetchContext) -> CollectResult:
        accept = "text/markdown, text/plain, text/html;q=0.9"
        if self.page.profile == "markdown_dated":
            accept = "text/markdown, text/plain;q=0.9, text/html;q=0.5"
        headers = etag_headers(ctx, {"Accept": accept})
        response = ctx.http.get(self.url, headers)
        if response.status == 304:
            return CollectResult(
                source_id=self.source_id,
                ok=True,
                not_modified=True,
                etag=response.header("ETag"),
                diff_mode=self.diff_mode,
            )
        if not response.ok:
            detail = response.error or ""
            if response.body:
                detail = response.body.decode("utf-8", "replace")[:180]
            return failure(self.source_id, self.diff_mode, f"http {response.status} {detail}".strip())
        raw = response.body or b""
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        if not raw.strip():
            return CollectResult(
                source_id=self.source_id, ok=False, empty=True, error="empty body", diff_mode=self.diff_mode
            )
        text = raw.decode("utf-8", "replace")
        try:
            if self.page.profile == "markdown_dated":
                rows = parse_markdown_dated(text, self.url)
            else:
                rows = parse_html_index(text, self.url, self.page.profile)
        except RemoteError as exc:
            return failure(self.source_id, self.diff_mode, str(exc))
        entries: list[Entry] = []
        for row in rows:
            plain = strip_tags(row["title"])
            kind, party = feed_decision(
                plain,
                self.page.filter,
                default_kind="official_release",
                first_party=True,
            )
            entries.append(
                Entry(
                    provider=self.provider,
                    model_id=row["model_id"],
                    title=plain or row["model_id"],
                    official_url=row["url"],
                    kind=kind,
                    first_party=party,
                    published_at=row.get("published") or None,
                    haystack=plain,
                )
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
