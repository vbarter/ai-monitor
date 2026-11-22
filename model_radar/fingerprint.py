"""Stable identity for a release.

The stored fingerprint is sha256 of the normalized preimage
`provider|model_id|version`. Same id with a new version is a new row.
"""

from __future__ import annotations

import hashlib
import re

_SPACE = re.compile(r"\s+")


def normalize(value: str | None) -> str:
    text = _SPACE.sub(" ", (value or "").strip().lower())
    return text


def preimage(provider: str, model_id: str, version: str | None) -> str:
    return f"{normalize(provider)}|{normalize(model_id)}|{normalize(version)}"


def fingerprint(provider: str, model_id: str, version: str | None) -> str:
    return hashlib.sha256(preimage(provider, model_id, version).encode("utf-8")).hexdigest()
