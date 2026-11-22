"""provider:model_id aliases.

The alias graph that merges an OpenRouter slug, a Hub repo, and a marketing
name is a later slice. The table is filled with the raw identity so that
slice can start from real rows.
"""

from __future__ import annotations


def alias_keys(provider: str, model_id: str, version: str) -> list[str]:
    keys = [f"{provider}:{model_id}"]
    if version:
        keys.append(f"{provider}:{model_id}:{version}")
    return keys
