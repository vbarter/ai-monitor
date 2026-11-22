"""RSS 2.0 and Atom reader for URLs listed in config.

Official blogs use `filter: release_keywords`. OpenRouter's model RSS uses
`filter: false` and `id_mode: openrouter` so the slug lines up with the JSON
catalog id.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

from model_radar.adapters.base import Adapter, CollectResult, Entry, FetchContext, etag_headers, failure
from model_radar.classify import feed_decision
from model_radar.config import FeedSpec
from model_radar.http import RemoteError, read_xml

_DATE_TITLE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TAGS = re.compile(r"<[^>]+>")
_HREF = re.compile(r"""href=["']([^"']+)["']""", re.I)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(elem: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in list(elem) if _local(child.tag) == name]


def _text(elem: ET.Element, name: str) -> str:
    nodes = _children(elem, name)
    if not nodes:
        return ""
    return (nodes[0].text or "").strip()


def _link(elem: ET.Element) -> str:
    for child in _children(elem, "link"):
        href = (child.attrib.get("href") or "").strip()
        text = (child.text or "").strip()
        rel = (child.attrib.get("rel") or "alternate").strip()
        if href and rel in {"alternate", ""}:
            return href
        if text:
            return text
    return ""


def parse_feed(xml_bytes: bytes) -> list[dict[str, str]]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise RemoteError(f"invalid feed xml: {exc}") from exc
    kind = _local(root.tag).lower()
    if kind == "rss":
        channels = [child for child in list(root) if _local(child.tag) == "channel"]
        nodes = []
        for channel in channels:
            nodes.extend(_children(channel, "item"))
    elif kind == "feed":
        nodes = _children(root, "entry")
    else:
        # A bare <channel> or a document we don't recognize.
        nodes = _children(root, "item") + _children(root, "entry")
    items = []
    for node in nodes:
        guid = _text(node, "guid") or _text(node, "id")
        title = _text(node, "title")
        link = _link(node)
        summary = (
            _text(node, "description")
            or _text(node, "summary")
            or _text(node, "encoded")
            or _text(node, "content")
        )
        published = _text(node, "pubDate") or _text(node, "published") or _text(node, "updated")
        items.append(
            {
                "guid": guid,
                "title": title,
                "link": link,
                "summary": summary,
                "published": published,
            }
        )
    return items


def _dated_release_title(title: str, summary_html: str, provider: str) -> str:
    """Z.ai's release RSS titles the item with the date. Pull a model slug from the body."""
    if not _DATE_TITLE.fullmatch((title or "").strip()):
        return title
    for href in _HREF.findall(summary_html or ""):
        slug = urlparse(href).path.strip("/").split("/")[-1]
        if slug and re.search(r"\d", slug) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,}", slug):
            return f"{slug} ({title.strip()})"
    return f"{provider} release {title.strip()}"


def openrouter_identity(link: str, guid: str) -> tuple[str, str]:
    path = urlparse(link).path.strip("/")
    model_id = path or guid
    version = guid if guid and model_id and guid != model_id else ""
    return model_id, version


class RssAdapter(Adapter):
    diff_mode = "feed"

    def __init__(self, feed: FeedSpec):
        self.feed = feed
        self.source_id = f"rss:{feed.id}"
        self.adapter = "rss"
        self.provider = feed.provider
        self.url = feed.url

    def fetch(self, ctx: FetchContext) -> CollectResult:
        headers = etag_headers(ctx, {"Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"})
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
            items = parse_feed(read_xml(response))
        except RemoteError as exc:
            return failure(self.source_id, self.diff_mode, str(exc))
        entries: list[Entry] = []
        for item in items:
            title = item["title"]
            summary = item["summary"]
            plain = " ".join(_TAGS.sub(" ", summary).split())
            title = _dated_release_title(title, summary, self.provider)
            blob = f"{title}\n{plain}"
            kind, first_party = feed_decision(
                blob,
                self.feed.filter,
                default_kind=self.feed.kind,
                first_party=self.feed.first_party,
            )
            if self.feed.id_mode == "openrouter":
                model_id, version = openrouter_identity(item["link"], item["guid"])
            else:
                guid = item["guid"]
                link = item["link"]
                if guid.startswith(("http://", "https://")):
                    model_id = guid
                else:
                    model_id = link or guid or title
                version = ""
            if not model_id:
                continue
            entries.append(
                Entry(
                    provider=self.provider,
                    model_id=model_id,
                    version=version,
                    title=title or model_id,
                    official_url=item["link"],
                    kind=kind,
                    first_party=first_party,
                    published_at=item["published"] or None,
                    haystack=blob,
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
