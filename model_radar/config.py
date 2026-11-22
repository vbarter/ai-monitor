"""Load config.yaml. Secrets stay in the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from model_radar.classify import DEFAULT_EXCLUDE, compile_patterns

DEFAULT_AUTHORS = (
    "Qwen",
    "deepseek-ai",
    "ByteDance-Seed",
    "XiaomiMiMo",
    "moonshotai",
    "zai-org",
    "MiniMaxAI",
    "tencent",
    "baidu",
    "meta-llama",
    "google",
    "openai",
)

IMPLEMENTED_PROVIDERS = {"openai", "anthropic", "gemini", "deepseek", "openrouter"}

DEFAULT_PROVIDERS = {
    "openai": {"env": "OPENAI_API_KEY"},
    "anthropic": {"env": "ANTHROPIC_API_KEY"},
    "gemini": {"env": "GEMINI_API_KEY"},
    "deepseek": {"env": "DEEPSEEK_API_KEY"},
    "openrouter": {"env": "OPENROUTER_API_KEY", "optional": True},
    "ark": {"env": "ARK_API_KEY"},
    "dashscope": {"env": "DASHSCOPE_API_KEY"},
    "moonshot": {"env": "MOONSHOT_API_KEY"},
    "zhipu": {"env": "ZHIPU_API_KEY"},
    "siliconflow": {"env": "SILICONFLOW_API_KEY"},
}

DEFAULT_RSS = (
    {
        "id": "openai-news",
        "provider": "openai",
        "url": "https://openai.com/news/rss.xml",
        "filter": "release_keywords",
    },
    {
        "id": "openai-release-notes",
        "provider": "openai",
        "url": "https://openai.com/products/release-notes/rss.xml",
        "filter": "release_keywords",
    },
    {
        "id": "deepmind",
        "provider": "google",
        "url": "https://deepmind.google/blog/rss.xml",
        "filter": "release_keywords",
    },
    {
        "id": "hf-blog",
        "provider": "huggingface",
        "url": "https://huggingface.co/blog/feed.xml",
        "filter": "release_keywords",
    },
    {
        "id": "qwen-legacy-blog",
        "provider": "alibaba",
        "url": "https://qwenlm.github.io/blog/index.xml",
        "filter": "release_keywords",
    },
    {
        "id": "openrouter-rss",
        "provider": "openrouter",
        "url": "https://openrouter.ai/api/v1/models?use_rss=true",
        "filter": False,
        "kind": "catalog_add",
        "first_party": False,
        "id_mode": "openrouter",
    },
)


@dataclass
class HuggingFaceConfig:
    enabled: bool = True
    authors: list[str] = field(default_factory=lambda: list(DEFAULT_AUTHORS))
    per_author_limit: int = 40
    exclude_patterns: list[str] = field(default_factory=lambda: [DEFAULT_EXCLUDE])
    token_env: str = "HF_TOKEN"


@dataclass
class ProviderSpec:
    name: str
    env: str
    optional: bool = False
    enabled: bool = True
    implemented: bool = True
    workspace_id: str | None = None


@dataclass
class FeedSpec:
    id: str
    provider: str
    url: str
    filter: str | bool = "release_keywords"
    kind: str = "official_release"
    first_party: bool = True
    id_mode: str = "link"
    enabled: bool = True


@dataclass
class ChangelogSpec:
    id: str
    provider: str
    url: str
    enabled: bool = False


@dataclass
class AlertConfig:
    slack_webhook_env: str = "SLACK_WEBHOOK_URL"
    slack_channel: str = "#自媒体"
    immediate_kinds: list[str] = field(
        default_factory=lambda: ["official_release", "weights_drop", "catalog_add"]
    )
    digest_hour: int = 8
    max_immediate_per_hour: int = 8
    router_catalog_immediate: bool = False


@dataclass
class Config:
    timezone: str = "Asia/Shanghai"
    database: str = "data/model_radar.sqlite"
    huggingface: HuggingFaceConfig = field(default_factory=HuggingFaceConfig)
    providers: dict[str, ProviderSpec] = field(default_factory=dict)
    rss: list[FeedSpec] = field(default_factory=list)
    changelogs: list[ChangelogSpec] = field(default_factory=list)
    alerts: AlertConfig = field(default_factory=AlertConfig)
    schedule: str = "0 * * * *"

    def exclude_regexes(self):
        return compile_patterns(self.huggingface.exclude_patterns)


def _providers_from(raw: dict) -> dict[str, ProviderSpec]:
    specs: dict[str, ProviderSpec] = {}
    for name, value in raw.items():
        value = value or {}
        implemented = name in IMPLEMENTED_PROVIDERS
        if "enabled" in value:
            enabled = bool(value.get("enabled"))
        else:
            enabled = implemented
        specs[name] = ProviderSpec(
            name=name,
            env=str(value.get("env") or f"{name.upper()}_API_KEY"),
            optional=bool(value.get("optional", False)),
            enabled=enabled,
            implemented=implemented,
            workspace_id=value.get("workspace_id"),
        )
    return specs


def _feeds_from(raw: list) -> list[FeedSpec]:
    feeds: list[FeedSpec] = []
    for item in raw:
        item = item or {}
        filter_value = item.get("filter", "release_keywords")
        first_party = item.get("first_party")
        if first_party is None:
            first_party = item.get("provider") != "openrouter"
        feeds.append(
            FeedSpec(
                id=str(item["id"]),
                provider=str(item.get("provider") or "unknown"),
                url=str(item["url"]),
                filter=filter_value if isinstance(filter_value, bool) else str(filter_value),
                kind=str(item.get("kind") or ("catalog_add" if filter_value is False else "official_release")),
                first_party=bool(first_party),
                id_mode=str(item.get("id_mode") or ("openrouter" if item.get("provider") == "openrouter" else "link")),
                enabled=bool(item.get("enabled", True)),
            )
        )
    return feeds


def load_config(path: str | Path) -> Config:
    file_path = Path(path)
    data = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{file_path} must be a mapping")

    zone = str(data.get("timezone") or "Asia/Shanghai")
    try:
        ZoneInfo(zone)
    except Exception as exc:
        raise ValueError(f"unknown timezone {zone}") from exc

    hf_raw = data.get("huggingface")
    if hf_raw is None:
        huggingface = HuggingFaceConfig()
    else:
        hf_raw = hf_raw or {}
        patterns = hf_raw.get("exclude_patterns") or [DEFAULT_EXCLUDE]
        huggingface = HuggingFaceConfig(
            enabled=bool(hf_raw.get("enabled", True)),
            authors=list(hf_raw.get("authors") or DEFAULT_AUTHORS),
            per_author_limit=int(hf_raw.get("per_author_limit") or 40),
            exclude_patterns=[str(item) for item in patterns],
            token_env=str(hf_raw.get("token_env") or "HF_TOKEN"),
        )
    # Fail at load if a pattern is invalid.
    compile_patterns(huggingface.exclude_patterns)

    if "providers" in data:
        providers = _providers_from(data.get("providers") or {})
    else:
        providers = _providers_from(DEFAULT_PROVIDERS)

    if "rss" in data:
        rss = _feeds_from(data.get("rss") or [])
    else:
        rss = _feeds_from(list(DEFAULT_RSS))

    changelogs: list[ChangelogSpec] = []
    for item in data.get("changelogs") or []:
        changelogs.append(
            ChangelogSpec(
                id=str(item["id"]),
                provider=str(item.get("provider") or "unknown"),
                url=str(item["url"]),
                enabled=bool(item.get("enabled", False)),
            )
        )

    alerts_raw = data.get("alerts") or {}
    kinds = alerts_raw.get("immediate_kinds") or ["official_release", "weights_drop", "catalog_add"]
    alerts = AlertConfig(
        slack_webhook_env=str(alerts_raw.get("slack_webhook_env") or "SLACK_WEBHOOK_URL"),
        slack_channel=str(alerts_raw.get("slack_channel") or "#自媒体"),
        immediate_kinds=[str(item) for item in kinds],
        digest_hour=int(alerts_raw.get("digest_hour") if alerts_raw.get("digest_hour") is not None else 8),
        max_immediate_per_hour=int(alerts_raw.get("max_immediate_per_hour") or 8),
        router_catalog_immediate=bool(alerts_raw.get("router_catalog_immediate", False)),
    )

    return Config(
        timezone=zone,
        database=str(data.get("database") or "data/model_radar.sqlite"),
        huggingface=huggingface,
        providers=providers,
        rss=rss,
        changelogs=changelogs,
        alerts=alerts,
        schedule=str(data.get("schedule") or "0 * * * *"),
    )


def resolve_db_path(config: Config, cli_path: str | None) -> str:
    if cli_path:
        return cli_path
    env_path = os.environ.get("MODEL_RADAR_DB")
    if env_path:
        return env_path
    return config.database


def resolve_config_path(cli_path: str | None) -> str:
    if cli_path:
        return cli_path
    return os.environ.get("MODEL_RADAR_CONFIG") or "config.yaml"
