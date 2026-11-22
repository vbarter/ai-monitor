import hashlib

from model_radar.fingerprint import fingerprint, normalize, preimage


def test_preimage_normalizes_case_and_space():
    assert normalize("  GPT-4o \n") == "gpt-4o"
    assert preimage("OpenAI", " GPT-4o ", None) == "openai|gpt-4o|"
    assert preimage("OpenAI", "GPT-4o", "") == preimage("openai", "gpt-4o", None)


def test_fingerprint_is_sha256_of_provider_model_version():
    raw = "openai|gpt-4o|2026-11-01"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert fingerprint("OpenAI", "GPT-4o", "2026-11-01") == digest
    assert fingerprint("OpenAI", "GPT-4o", "2026-11-02") != digest
