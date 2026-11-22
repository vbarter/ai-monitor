"""Noise rules and RSS release-keyword checks.

Quants, LoRAs, adapters, and user fine-tunes are not immediate alerts.
Official blog items alert only when a release verb lines up with a model
family token, so a lab's ordinary research posts stay in the digest bucket.
"""

from __future__ import annotations

import re

DEFAULT_EXCLUDE = (
    r"(?i)(gguf|awq|gptq|mlx|exl2|exl3|onnx|bnb|fp8|fp4|int4|int8|lora|adapter|\d+bit|\d+(?:\.\d+)?bpw)"
)

# Latin tokens use boundaries so "gpt" does not fire inside "chatgpt".
_VERBS = (
    "release",
    "released",
    "launch",
    "launched",
    "introducing",
    "introduce",
    "announcing",
    "announce",
    "announced",
    "open-source",
    "opensource",
)
_FAMILIES = (
    "gpt",
    "claude",
    "gemini",
    "llama",
    "qwen",
    "deepseek",
    "mimo",
    "hunyuan",
    "ernie",
    "kimi",
    "glm",
    "minimax",
    "doubao",
    "model",
    "models",
    "weights",
)
_STRONG = (
    "open-weight",
    "open weight",
    "model release",
    "新模型",
    "模型发布",
    "开源权重",
)
_ZH_VERBS = ("发布", "开源")
_ZH_FAMILIES = ("模型", "权重")

_FINETUNE = re.compile(r"(?i)(?:^ft:|(?:^|[/_:])ft[:_-])")


def compile_patterns(patterns: list[str] | tuple[str, ...]) -> list[re.Pattern[str]]:
    compiled: list[re.Pattern[str]] = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern))
        except re.error as exc:
            raise ValueError(f"invalid exclude pattern {pattern!r}: {exc}") from exc
    return compiled


def _latin(text: str, token: str) -> bool:
    # Digits may follow ("qwen3", "gpt5"). A preceding letter still blocks "chatgpt".
    return re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z])", text) is not None


def release_keyword_hit(text: str) -> bool:
    raw = text or ""
    low = raw.lower()
    if any(phrase in low or phrase in raw for phrase in _STRONG):
        return True
    verb = any(_latin(low, token) for token in _VERBS) or any(token in raw for token in _ZH_VERBS)
    family = any(_latin(low, token) for token in _FAMILIES) or any(token in raw for token in _ZH_FAMILIES)
    return verb and family


# Official news indexes are already curated. A leading "Introducing" or a
# version token (K3, V4.1, M3.1) is enough; a bare family name is not, so
# "the making of Claude Code" stays a digest note.
_LEAD_RELEASE = re.compile(r"(?i)^(?:introducing|announcing|releasing|released|发布|开源)\b")
_VERSION_TOKEN = re.compile(
    r"(?i)(?:\b(?:deepseek|qwen|claude|gemini|llama|kimi|mimo|minimax|glm|gpt|hunyuan|ernie|doubao|seed)[- ]?\d|\b[vkmh]\d+(?:\.\d+)?|\d+\.\d+)"
)


def official_page_hit(text: str) -> bool:
    raw = (text or "").strip()
    if release_keyword_hit(raw):
        return True
    if _LEAD_RELEASE.search(raw):
        return True
    return bool(_VERSION_TOKEN.search(raw))


def feed_decision(text: str, mode, *, default_kind: str, first_party: bool) -> tuple[str, bool]:
    """Return (kind, first_party) for one RSS or changelog item."""
    if mode in (False, "false", "", None):
        kind = default_kind
        party = bool(first_party) and kind in {"official_release", "catalog_add", "weights_drop"}
        return kind, party
    matched = official_page_hit(text) if mode == "official_page" else release_keyword_hit(text)
    kind = "official_release" if matched else "changelog_note"
    return kind, bool(first_party) and matched


def is_user_finetune(model_id: str) -> bool:
    return bool(_FINETUNE.search(model_id or ""))


def is_noise(model_id: str, *extra: str, patterns: list[re.Pattern[str]]) -> bool:
    if is_user_finetune(model_id):
        return True
    blob = " ".join([model_id or "", *extra])
    return any(pattern.search(blob) for pattern in patterns)
