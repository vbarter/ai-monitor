"""Slack incoming webhooks. One post per attempt; failures stay pending."""

from __future__ import annotations

import json
import logging

from model_radar.db import Database
from model_radar.http import HttpClient
from model_radar.integrate import ReleaseNotice
from model_radar.redact import clean_error
from model_radar.timeutil import format_zone

log = logging.getLogger(__name__)


def render_notice(notice: ReleaseNotice, zone: str) -> str:
    lines = [
        f"*[{notice.kind}]* {notice.title or notice.model_id}",
        f"提供方: {notice.provider}",
        f"模型: {notice.model_id}",
    ]
    if notice.version:
        lines.append(f"版本: {notice.version}")
    lines.append(f"首次发现: {format_zone(notice.first_seen_at, zone)}")
    lines.append(f"来源: {notice.source_id}")
    if notice.official_url:
        lines.append(f"链接: {notice.official_url}")
    return "\n".join(lines)


def render_batch(notices: list[ReleaseNotice], zone: str) -> str:
    lines = [f"model_radar: 本小时 {len(notices)} 条新发布，已合并为一条"]
    for index, notice in enumerate(notices, 1):
        url = f" {notice.official_url}" if notice.official_url else ""
        lines.append(f"{index}. [{notice.kind}] {notice.provider} {notice.model_id}{url}")
    return "\n".join(lines)


def post_webhook(http: HttpClient, url: str, payload: dict) -> tuple[bool, str | None]:
    if not url.startswith("https://"):
        return False, "webhook must be https"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    response = http.post(url, body, {"Content-Type": "application/json; charset=utf-8"})
    if response.ok:
        return True, None
    detail = response.error or ""
    if response.body:
        detail = response.body.decode("utf-8", "replace")
    return False, clean_error(f"http {response.status} {detail}".strip())


def dispatch_alerts(db: Database, http: HttpClient, webhook_url: str | None) -> int:
    alerts = db.list_open_alerts()
    if not alerts:
        return 0
    if not webhook_url:
        for alert in alerts:
            db.set_alert(alert["id"], "failed", "SLACK_WEBHOOK_URL unset")
        db.commit()
        log.warning("SLACK_WEBHOOK_URL unset; %s alert(s) left for retry", len(alerts))
        return 0
    sent = 0
    for alert in alerts:
        body = json.loads(alert["body_json"] or "{}")
        payload = {"text": body.get("text") or ""}
        channel = body.get("channel")
        if channel:
            payload["channel"] = channel
        ok, err = post_webhook(http, webhook_url, payload)
        if ok:
            db.set_alert(alert["id"], "sent", None)
            notified_as = body.get("notified_as") or "immediate"
            for fp in body.get("fingerprints") or []:
                db.set_notified(fp, notified_as, only_if_null=True)
            if body.get("event_ids"):
                db.set_events_alert(list(body["event_ids"]), alert["id"])
            if body.get("health") and body.get("source_id"):
                db.set_health_notified(body["source_id"], 1)
            if body.get("clear_recoveries"):
                db.clear_recovery(list(body["clear_recoveries"]))
            sent += 1
        else:
            db.set_alert(alert["id"], "failed", err)
            log.warning("slack alert %s failed: %s", alert["id"], err)
    db.commit()
    return sent
