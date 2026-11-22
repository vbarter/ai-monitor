"""UTC storage and Asia/Shanghai display."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_stamp(moment: datetime | None = None) -> str:
    moment = moment or utc_now()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(value: str | None) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def format_zone(stamp: str, zone: str) -> str:
    parsed = parse_time(stamp) or utc_now()
    local = parsed.astimezone(ZoneInfo(zone))
    return local.strftime("%Y-%m-%d %H:%M") + f" {zone}"
