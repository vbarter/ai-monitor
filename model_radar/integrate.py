"""Turn a successful fetch into seen rows.

Quiet baseline: the first successful fetch of a source records rows and does
not alert. A later fetch alerts only for fingerprints that were not seen.
An empty or failed body never reaches this module, so the stored snapshot
stays put.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass

from model_radar.adapters.base import Adapter, CollectResult, Entry
from model_radar.aliases import alias_keys
from model_radar.classify import is_noise
from model_radar.db import Database
from model_radar.fingerprint import fingerprint, preimage
from model_radar.timeutil import parse_time

log = logging.getLogger(__name__)


@dataclass
class ReleaseNotice:
    fingerprint: str
    provider: str
    model_id: str
    version: str
    kind: str
    title: str
    official_url: str
    first_seen_at: str
    source_id: str


def snapshot_row(entry: Entry) -> dict:
    return {
        "context_length": entry.context_length,
        "kind": entry.kind,
        "model_id": entry.model_id,
        "official_url": entry.official_url,
        "pricing": entry.pricing or None,
        "provider": entry.provider,
        "published_at": entry.published_at,
        "title": entry.title,
        "version": entry.version or "",
    }


def document_hash(rows: list[dict]) -> str:
    blob = json.dumps(rows, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _price(value: dict | None) -> dict | None:
    if not value:
        return None
    kept = {}
    for key in ("prompt", "completion"):
        if value.get(key) is not None:
            kept[key] = str(value[key])
    return kept or None


def meta_changed(previous: dict, entry: Entry) -> bool:
    if _price(previous.get("pricing")) != _price(entry.pricing):
        return True
    old_context = previous.get("context_length")
    new_context = entry.context_length
    return old_context != new_context


def prepare(entry: Entry, patterns) -> Entry:
    extra = [entry.haystack] if entry.kind == "weights_drop" and entry.haystack else []
    if is_noise(entry.model_id, entry.version or "", *extra, patterns=patterns):
        entry.kind = "noise"
    return entry


def _notice_from_entry(fp: str, entry: Entry, source_id: str, first_seen_at: str) -> ReleaseNotice:
    return ReleaseNotice(
        fingerprint=fp,
        provider=entry.provider,
        model_id=entry.model_id,
        version=entry.version or "",
        kind=entry.kind,
        title=entry.title or entry.model_id,
        official_url=entry.official_url,
        first_seen_at=first_seen_at,
        source_id=source_id,
    )


def notice_from_row(row: dict) -> ReleaseNotice:
    return ReleaseNotice(
        fingerprint=row["fingerprint"],
        provider=row["provider"],
        model_id=row["model_id"],
        version=row.get("version") or "",
        kind=row["kind"],
        title=row.get("title") or row["model_id"],
        official_url=row.get("official_url") or "",
        first_seen_at=row["first_seen_at"],
        source_id=row.get("first_source") or "",
    )


def integrate_source(
    db: Database,
    adapter: Adapter,
    result: CollectResult,
    *,
    patterns,
    now: str,
) -> list[ReleaseNotice]:
    prepared = [prepare(entry, patterns) for entry in result.entries]
    for entry in prepared:
        if not entry.provider:
            entry.provider = adapter.provider
    rows = sorted(
        (snapshot_row(entry) for entry in prepared),
        key=lambda row: (row["provider"], row["model_id"], row["version"]),
    )
    digest = document_hash(rows)
    source = db.ensure_source(adapter.source_id, adapter.adapter, adapter.provider, adapter.url)
    if source["last_ok_at"] and source["last_snapshot_hash"] == digest:
        db.mark_ok(adapter.source_id, now=now, etag=result.etag, snapshot_hash=digest)
        return []

    baseline = not source["last_ok_at"]
    cursor_dt = parse_time(source.get("cursor"))
    previous: dict[str, dict] = {}
    if result.diff_mode == "catalog":
        stored = db.get_snapshot(adapter.source_id)
        if stored:
            for row in stored["body"].get("entries") or []:
                previous[row["model_id"]] = row

    upgrades: list[ReleaseNotice] = []
    for entry in prepared:
        try:
            notice = _apply_entry(
                db,
                entry,
                source_id=adapter.source_id,
                diff_mode=result.diff_mode,
                baseline=baseline,
                cursor_dt=cursor_dt,
                previous=previous,
                now=now,
            )
        except Exception:
            log.exception("skip entry %s from %s", entry.model_id, adapter.source_id)
            continue
        if notice is not None:
            upgrades.append(notice)

    moments = [parse_time(entry.published_at) for entry in prepared]
    moments = [moment for moment in moments if moment is not None]
    if cursor_dt is not None:
        moments.append(cursor_dt)
    new_cursor = max(moments).strftime("%Y-%m-%dT%H:%M:%SZ") if moments and result.diff_mode == "cursor" else None
    db.save_snapshot(
        adapter.source_id,
        {"entries": rows},
        digest,
        now,
    )
    db.mark_ok(
        adapter.source_id,
        now=now,
        etag=result.etag,
        cursor=new_cursor,
        snapshot_hash=digest,
    )
    return upgrades


def _insert_new(
    db: Database,
    *,
    fp: str,
    entry: Entry,
    source_id: str,
    now: str,
    notified: str | None,
    raw: str,
) -> None:
    db.insert_seen(
        {
            "fingerprint": fp,
            "canonical_id": preimage(entry.provider, entry.model_id, entry.version),
            "provider": entry.provider,
            "model_id": entry.model_id,
            "version": entry.version or "",
            "kind": entry.kind,
            "title": entry.title or entry.model_id,
            "official_url": entry.official_url,
            "first_seen_at": now,
            "last_seen_at": now,
            "first_source": source_id,
            "notified": notified,
            "raw_hash": raw,
            "first_party": 1 if entry.first_party else 0,
            "upgraded": 0,
        }
    )
    for alias in alias_keys(entry.provider, entry.model_id, entry.version or ""):
        db.remember_alias(alias, fp)
    db.insert_event(
        fp,
        source_id,
        now,
        {
            "kind": entry.kind,
            "model_id": entry.model_id,
            "version": entry.version or "",
            "title": entry.title,
            "official_url": entry.official_url,
        },
    )


def _apply_entry(
    db: Database,
    entry: Entry,
    *,
    source_id: str,
    diff_mode: str,
    baseline: bool,
    cursor_dt,
    previous: dict,
    now: str,
) -> ReleaseNotice | None:
    fp = fingerprint(entry.provider, entry.model_id, entry.version)
    raw = hashlib.sha256(
        json.dumps(snapshot_row(entry), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    existing = db.get_seen(fp)
    published = parse_time(entry.published_at) if diff_mode == "cursor" else None

    if diff_mode == "cursor" and not baseline:
        historical = published is None or (cursor_dt is not None and published <= cursor_dt)
        if historical:
            if existing is None:
                _insert_new(
                    db,
                    fp=fp,
                    entry=entry,
                    source_id=source_id,
                    now=now,
                    notified="suppressed" if entry.kind == "noise" else "baseline",
                    raw=raw,
                )
            else:
                db.touch_seen(fp, now)
                if not db.has_event_from(fp, source_id):
                    db.insert_event(fp, source_id, now, {"kind": entry.kind, "model_id": entry.model_id})
            return None

    if diff_mode == "catalog":
        prev = previous.get(entry.model_id)
        if prev is not None and (prev.get("version") or "") == (entry.version or ""):
            if existing is None:
                _insert_new(
                    db,
                    fp=fp,
                    entry=entry,
                    source_id=source_id,
                    now=now,
                    notified="suppressed" if entry.kind == "noise" else "baseline",
                    raw=raw,
                )
            else:
                db.touch_seen(fp, now)
                if not db.has_event_from(fp, source_id):
                    db.insert_event(fp, source_id, now, {"kind": entry.kind, "model_id": entry.model_id})
                if meta_changed(prev, entry):
                    db.insert_event(
                        fp,
                        source_id,
                        now,
                        {
                            "kind": "catalog_update",
                            "model_id": entry.model_id,
                            "version": entry.version or "",
                            "pricing": entry.pricing,
                            "context_length": entry.context_length,
                        },
                    )
            return None

    if existing is not None:
        db.touch_seen(fp, now)
        if not db.has_event_from(fp, source_id):
            db.insert_event(
                fp,
                source_id,
                now,
                {
                    "kind": entry.kind,
                    "model_id": entry.model_id,
                    "official_url": entry.official_url,
                },
            )
        if existing["kind"] == "catalog_add" and entry.kind == "official_release" and not existing["upgraded"]:
            db.update_kind(fp, "official_release", upgraded=1)
            if existing["notified"] in (None, "immediate", "digest"):
                if existing["notified"] is None:
                    return None
                return _notice_from_entry(fp, entry, source_id, existing["first_seen_at"])
        return None

    if entry.kind == "noise":
        notified = "suppressed"
    elif baseline:
        notified = "baseline"
    else:
        notified = None
    _insert_new(
        db,
        fp=fp,
        entry=entry,
        source_id=source_id,
        now=now,
        notified=notified,
        raw=raw,
    )
    return None
