from model_radar.http import HttpClient, HttpResponse


def _response(status, url="https://example.test/x"):
    return HttpResponse(status=status, body=b"{}", headers={}, url=url)


def test_retries_transient_then_succeeds():
    calls = {"n": 0}

    def transport(method, url, headers, body, timeout):
        calls["n"] += 1
        if calls["n"] < 3:
            return _response(503, url)
        return _response(200, url)

    client = HttpClient(transport=transport, sleeper=lambda _s: None, attempts=3, timeout=1)
    response = client.get("https://example.test/x")
    assert response.status == 200
    assert calls["n"] == 3


def test_does_not_retry_forbidden_or_rate_limit():
    calls = {"n": 0}

    def transport(method, url, headers, body, timeout):
        calls["n"] += 1
        return _response(403 if calls["n"] == 1 else 200, url)

    client = HttpClient(transport=transport, sleeper=lambda _s: None, attempts=3, timeout=1)
    assert client.get("https://example.test/x").status == 403
    assert calls["n"] == 1

    def limited(method, url, headers, body, timeout):
        calls["n"] += 1
        return _response(429, url)

    client.transport = limited
    assert client.get("https://example.test/x").status == 429
    assert calls["n"] == 2


def test_post_is_single_shot():
    calls = {"n": 0}

    def transport(method, url, headers, body, timeout):
        calls["n"] += 1
        assert method == "POST"
        return _response(500, url)

    client = HttpClient(transport=transport, sleeper=lambda _s: None, attempts=3, timeout=1)
    assert client.post("https://hooks.slack.com/services/T/B/X", b"{}").status == 500
    assert calls["n"] == 1
