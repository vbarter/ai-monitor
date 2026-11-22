from model_radar.redact import clean_error


def test_clean_error_strips_bearer_and_limits_length():
    text = "Authorization: Bearer sk-live-secret failed " + ("x" * 400)
    cleaned = clean_error(text, limit=80)
    assert "sk-live-secret" not in cleaned
    assert "[redacted]" in cleaned
    assert len(cleaned) <= 80
