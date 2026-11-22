import pytest

from model_radar.classify import compile_patterns, is_noise, is_user_finetune, release_keyword_hit
from model_radar.classify import DEFAULT_EXCLUDE


PATTERNS = compile_patterns([DEFAULT_EXCLUDE])


@pytest.mark.parametrize(
    "text, hit",
    [
        ("Introducing Gemini 4 Neon", True),
        ("发布 Qwen3", True),
        ("Introducing apps in ChatGPT", False),
        ("Robotics research notes", False),
        ("A new paper on control", False),
        ("Weekly changelog typo fix", False),
        ("Open-weight model release", True),
    ],
)
def test_release_keywords(text, hit):
    assert release_keyword_hit(text) is hit


def test_quant_lora_and_finetune_are_noise():
    assert is_noise("Qwen/Qwen3-8B-GGUF", patterns=PATTERNS)
    assert is_noise("Qwen/Qwen3-8B", "lora", patterns=PATTERNS)
    assert is_noise("org/model-awq", patterns=PATTERNS)
    assert is_noise("org/model-4bit", patterns=PATTERNS)
    assert not is_noise("Qwen/Qwen3-8B", "text-generation safetensors", patterns=PATTERNS)
    assert is_user_finetune("ft:gpt-4o:acme:custom:abc")
    assert is_noise("ft:gpt-4o:acme:custom:abc", patterns=PATTERNS)
    assert not is_user_finetune("gpt-4o")
