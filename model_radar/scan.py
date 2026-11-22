"""One scan: fetch every enabled MVP source, then send pending Slack alerts."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass

from model_radar.adapters.base import FetchContext
from model_radar.adapters.registry import build_sources
from model_radar.config import Config
from model_radar.db import Database
from model_radar.http import HttpClient
from model_radar.integrate import ReleaseNotice, integrate_source, notice_from_row
from model_radar.notify.slack import dispatch_alerts, render_batch, render_notice
from model_radar.redact import clean_error
from model_radar.timeutil import utc_stamp

log = logging.getLogger(__name__)
HEALTH_AFTER = 3


@dataclass
class ScanStats:
    sources: int
    ok: int
    failed: int
    skipped: int
    new_releases: int
    alerts_sent: int


def run_scan(
    config: Config,
    db: Database,
    http: HttpClient,
    *,
    only: set[str] | None = None,
    env: Mapping | None = None,
    now: str | None = None,
) -> ScanStats:
    environ = env if env is not None else os.environ
    moment = now or utc_stamp()
    patterns = config.exclude_regexes()
    adapters, notes = build_sources(config, environ)
    if only:
        adapters = [adapter for adapter in adapters if adapter.source_id in only]
        notes = [note for note in notes if any(token in note for token in only)]
    for note in notes:
        log.info(note)

    fetched = []
    for adapter in adapters:
        log.info("fetch %s", adapter.source_id)
        result = adapter.collect(FetchContext(http, previous_etag=_previous_etag(db, adapter.source_id)))
        fetched.append((adapter, result))

    upgrades: list[ReleaseNotice] = []
    failed_ids: list[str] = []
    ok_count = 0
    try:
        for adapter, result in fetched:
            db.ensure_source(adapter.source_id, adapter.adapter, adapter.provider, adapter.url)
            if result.not_modified:
                source = db.get_source(adapter.source_id)
                if source["last_ok_at"]:
                    db.mark_ok(adapter.source_id, now=moment, etag=result.etag)
                    ok_count += 1
                else:
                    db.mark_failure(adapter.source_id, "304 before baseline")
                    failed_ids.append(adapter.source_id)
                    log.warning("source %s failed: 304 before baseline", adapter.source_id)
                continue
            if not result.ok or result.empty or result.incomplete or not result.entries:
                message = result.error or "empty body"
                db.mark_failure(adapter.source_id, message)
                failed_ids.append(adapter.source_id)
                log.warning("source %s failed: %s", adapter.source_id, message)
                continue
            try:
                upgrades.extend(
                    integrate_source(db, adapter, result, patterns=patterns, now=moment)
                )
                ok_count += 1
            except Exception as exc:
                log.exception("integrate %s", adapter.source_id)
                db.mark_failure(adapter.source_id, clean_error(str(exc)))
                failed_ids.append(adapter.source_id)
        notices = _pending_notices(db, upgrades, config.alerts.router_catalog_immediate)
        _queue_releases(db, notices, config, moment)
        _queue_health(db, failed_ids, config, moment)
        db.commit()
    except Exception:
        db.rollback()
        raise

    webhook = (environ.get(config.alerts.slack_webhook_env) or "").strip() or None
    sent = dispatch_alerts(db, http, webhook)
    log.info(
        "scan done sources=%s ok=%s failed=%s skipped=%s new=%s sent=%s",
        len(adapters),
        ok_count,
        len(failed_ids),
        len(notes),
        len(notices),
        sent,
    )
    return ScanStats(
        sources=len(adapters),
        ok=ok_count,
        failed=len(failed_ids),
        skipped=len(notes),
        new_releases=len(notices),
        alerts_sent=sent,
    )


def _previous_etag(db: Database, source_id: str) -> str | None:
    row = db.conn.execute(
        "SELECT last_etag, last_ok_at FROM sources WHERE id = ?",
        (source_id,),
    ).fetchone()
    if row is None or not row["last_ok_at"]:
        return None
    return row["last_etag"]


def _pending_notices(db: Database, upgrades: list[ReleaseNotice], router_immediate: bool) -> list[ReleaseNotice]:
    open_fps, sent_fps = _alert_fingerprints(db)
    notices: dict[str, ReleaseNotice] = {}
    for row in db.list_immediate_pending(router_catalog_immediate=router_immediate):
        fp = row["fingerprint"]
        if fp in open_fps:
            continue
        if fp in sent_fps:
            db.set_notified(fp, "immediate", only_if_null=True)
            continue
        notices[fp] = notice_from_row(row)
    for notice in upgrades:
        if notice.fingerprint in open_fps or notice.fingerprint in sent_fps:
            continue
        current = notices.get(notice.fingerprint)
        if current is None or notice.kind == "official_release":
            notices[notice.fingerprint] = notice
    return list(notices.values())


def _alert_fingerprints(db: Database) -> tuple[set[str], set[str]]:
    import json

    open_fps: set[str] = set()
    sent_fps: set[str] = set()
    rows = db.conn.execute("SELECT status, body_json FROM alerts").fetchall()
    for row in rows:
        body = json.loads(row["body_json"] or "{}")
        if row["status"] in ("pending", "failed"):
            bucket = open_fps
        elif row["status"] == "sent":
            bucket = sent_fps
        else:
            continue
        for fp in body.get("fingerprints") or []:
            bucket.add(fp)
    return open_fps, sent_fps


def _queue_releases(db: Database, notices: list[ReleaseNotice], config: Config, now: str) -> None:
    if not notices:
        return
    channel = config.alerts.slack_channel
    zone = config.timezone
    limit = config.alerts.max_immediate_per_hour
    if len(notices) > limit:
        db.insert_alert(
            created_at=now,
            channel="slack",
            status="pending",
            body={
                "text": render_batch(notices, zone),
                "fingerprints": [notice.fingerprint for notice in notices],
                "health": False,
                "notified_as": "immediate",
                "channel": channel,
            },
        )
        return
    for notice in notices:
        db.insert_alert(
            created_at=now,
            channel="slack",
            status="pending",
            body={
                "text": render_notice(notice, zone),
                "fingerprints": [notice.fingerprint],
                "health": False,
                "notified_as": "immediate",
                "channel": channel,
            },
        )


def _queue_health(db: Database, failed_ids: list[str], config: Config, now: str) -> None:
    already = db.open_health_source_ids()
    for source_id in failed_ids:
        source = db.get_source(source_id)
        if source["consecutive_failures"] < HEALTH_AFTER or source["health_notified"]:
            continue
        if source_id in already:
            continue
        already.add(source_id)
        error = source.get("last_error") or "unknown"
        text = (
            f"来源 {source_id} 连续失败 {source['consecutive_failures']} 次，"
            f"未用失败结果覆盖已有快照。\n最近错误: {error}"
        )
        db.insert_alert(
            created_at=now,
            channel="slack",
            status="pending",
            body={
                "text": text,
                "fingerprints": [],
                "health": True,
                "source_id": source_id,
                "notified_as": "immediate",
                "channel": config.alerts.slack_channel,
            },
        )


if __name__ == "__main__":
    import sys

    from model_radar.cli import main

    raise SystemExit(main(["scan", *sys.argv[1:]]))
