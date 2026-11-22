"""Digest command.

The hourly timer does not run this. Bundling at 08:00 Asia/Shanghai is a
later slice; `digest` is the hook that slice will call. Without `--send` it
only prints. It never posts an empty "no news" message.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping

from model_radar.config import Config
from model_radar.db import Database
from model_radar.http import HttpClient
from model_radar.notify.slack import dispatch_alerts
from model_radar.timeutil import format_zone, utc_stamp

log = logging.getLogger(__name__)


def _render(db: Database, zone: str) -> tuple[str, dict]:
    seen = db.digest_seen()
    updates = db.digest_updates()
    recoveries = db.pending_recoveries()
    if not seen and not updates and not recoveries:
        return "", {}
    lines = ["model_radar digest"]
    for row in seen:
        when = format_zone(row["first_seen_at"], zone)
        url = f" {row['official_url']}" if row.get("official_url") else ""
        lines.append(
            f"- [{row['kind']}] {row['provider']} {row.get('title') or row['model_id']} {when}{url}".rstrip()
        )
    for event in updates:
        payload = event["payload"]
        lines.append(
            f"- [catalog_update] {payload.get('model_id') or event['fingerprint']} via {event['source_id']}"
        )
    for recovery in recoveries:
        lines.append(f"- [recovered] {recovery['recovery_pending']}")
    body = {
        "text": "\n".join(lines),
        "fingerprints": [row["fingerprint"] for row in seen],
        "event_ids": [event["id"] for event in updates],
        "clear_recoveries": [recovery["id"] for recovery in recoveries],
        "health": False,
        "notified_as": "digest",
        "channel": "",
    }
    return body["text"], body


def run_digest(
    config: Config,
    db: Database,
    http: HttpClient,
    *,
    send: bool,
    now: str | None = None,
    env: Mapping | None = None,
) -> int:
    text, body = _render(db, config.timezone)
    if not text:
        log.info("digest: nothing pending")
        return 0
    if not send:
        print(text)
        return 0
    body["channel"] = config.alerts.slack_channel
    open_digest = False
    for alert in db.list_open_alerts():
        payload = json.loads(alert["body_json"] or "{}")
        if payload.get("notified_as") == "digest":
            open_digest = True
            break
    if not open_digest:
        db.insert_alert(created_at=now or utc_stamp(), channel="slack", status="pending", body=body)
        db.commit()
    environ = env if env is not None else os.environ
    webhook = (environ.get(config.alerts.slack_webhook_env) or "").strip() or None
    dispatch_alerts(db, http, webhook)
    return 0
