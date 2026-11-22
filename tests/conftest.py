import pytest

from model_radar.http import urllib_transport


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("live network is disabled in tests")

    monkeypatch.setattr("model_radar.http.urllib_transport", boom)
    # HttpClient binds the transport at init, so patch the name it looks up.
    monkeypatch.setattr(urllib_transport.__module__ + ".urllib_transport", boom)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in (
        "SLACK_WEBHOOK_URL",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENROUTER_API_KEY",
        "HF_TOKEN",
        "ARK_API_KEY",
        "DASHSCOPE_API_KEY",
        "MOONSHOT_API_KEY",
        "ZHIPU_API_KEY",
        "SILICONFLOW_API_KEY",
        "MODEL_RADAR_DB",
        "MODEL_RADAR_CONFIG",
    ):
        monkeypatch.delenv(key, raising=False)
