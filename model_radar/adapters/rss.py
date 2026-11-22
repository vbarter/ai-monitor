"""RSS 2.0 and Atom reader for URLs listed in config.

Official blogs use `filter: release_keywords`. OpenRouter's model RSS uses
`filter: false` and `id_mode: openrouter` so the slug lines up with the JSON
catalog id.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from urllib.parse import urlparse

from model_radar.adapters.base import Adapter, CollectResult, Entry, FetchContext, etag_headers, failure
from model_radar.classify import release_keyword_hit
from model_radar.config import FeedSpec
from model_radar.http import RemoteError, read_xml


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
        summary = _text(node, "description") or _text(node, "summary") or _text(node, "content")
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
            blob = f"{title}\n{summary}"
            if self.feed.filter == "release_keywords":
                matched = release_keyword_hit(blob)
                kind = "official_release" if matched else "changelog_note"
                first_party = bool(self.feed.first_party) and matched
            else:
                kind = self.feed.kind
                first_party = bool(self.feed.first_party) and kind in {
                    "official_release",
                    "catalog_add",
                    "weights_drop",
                }
            if self.feed.id_mode == "openrouter":
                model_id, version = openrouter_identity(item["link"], item["guid"])
            else:
                model_id = item["guid"] or item["link"] or title
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
