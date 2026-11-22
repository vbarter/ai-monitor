"""Build the MVP adapter list from config. Later slices are skipped, not fetched."""

from __future__ import annotations

from collections.abc import Mapping

from model_radar.adapters.anthropic import AnthropicAdapter
from model_radar.adapters.base import Adapter
from model_radar.adapters.deepseek import DeepSeekAdapter
from model_radar.adapters.gemini import GeminiAdapter
from model_radar.adapters.html_changelog import HtmlChangelogAdapter
from model_radar.adapters.huggingface import HuggingFaceOrgAdapter
from model_radar.adapters.openai import OpenAIAdapter
from model_radar.adapters.openrouter import OpenRouterAdapter
from model_radar.adapters.rss import RssAdapter
from model_radar.config import Config


def build_sources(config: Config, env: Mapping) -> tuple[list[Adapter], list[str]]:
    notes: list[str] = []
    adapters: list[Adapter] = []
    if config.huggingface.enabled:
        token = (env.get(config.huggingface.token_env) or "").strip() or None
        for author in config.huggingface.authors:
            name = str(author).strip()
            if not name:
                continue
            adapters.append(
                HuggingFaceOrgAdapter(name, limit=config.huggingface.per_author_limit, token=token)
            )
    for spec in config.providers.values():
        if not spec.enabled:
            continue
        if not spec.implemented:
            notes.append(f"skip {spec.name}: later slice, not in this build")
            continue
        key = (env.get(spec.env) or "").strip()
        if not key and not spec.optional:
            notes.append(f"skip {spec.name}: {spec.env} unset")
            continue
        adapters.append(_provider(spec.name, key))
    for feed in config.rss:
        if feed.enabled:
            adapters.append(RssAdapter(feed))
    for page in config.changelogs:
        if page.enabled:
            adapters.append(HtmlChangelogAdapter(page))
    return adapters, notes


def _provider(name: str, key: str) -> Adapter:
    if name == "openrouter":
        return OpenRouterAdapter(api_key=key or None)
    if name == "openai":
        return OpenAIAdapter(key)
    if name == "anthropic":
        return AnthropicAdapter(key)
    if name == "gemini":
        return GeminiAdapter(key)
    if name == "deepseek":
        return DeepSeekAdapter(key)
    raise ValueError(f"no MVP adapter for {name}")
