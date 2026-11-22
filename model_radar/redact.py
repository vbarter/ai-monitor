"""Keep secrets out of stored errors and logs."""

from __future__ import annotations

import re

_BEARER = re.compile(r"(?i)\bbearer\s+\S+")
_AUTH = re.compile(r"(?i)\bauthorization\s*[:=]\s*(?:bearer\s+)?\S+")
_KV = re.compile(r"(?i)\b(api[_-]?key|token|webhook)\s*[:=]\s*\S+")


def clean_error(text: str, limit: int = 300) -> str:
    cleaned = _BEARER.sub("Bearer [redacted]", text or "")
    cleaned = _AUTH.sub("authorization: [redacted]", cleaned)
    cleaned = _KV.sub(lambda match: f"{match.group(1)}=[redacted]", cleaned)
    cleaned = cleaned.replace("\n", " ").strip()
    if len(cleaned) > limit:
        return cleaned[: limit - 3] + "..."
    return cleaned
