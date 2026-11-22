"""Small HTTP client. GET retries; POST does not.

429 is not retried inside the hour. The caller records the failure and the
next timer run tries again. POST is one shot so a Slack failure stays on the
alert row for the next scan.
"""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

from model_radar import __version__

USER_AGENT = f"model-radar/{__version__} (+https://github.com/vbarter/ai-monitor)"
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.1",
}
RETRY_STATUS = {408, 425, 500, 502, 503, 504}


class RemoteError(Exception):
    pass


@dataclass
class HttpResponse:
    status: int
    body: bytes
    headers: dict[str, str]
    url: str
    error: str | None = None

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def header(self, name: str) -> str | None:
        for key, value in self.headers.items():
            if key.lower() == name.lower():
                return value
        return None


Transport = Callable[[str, str, dict[str, str], bytes | None, float], HttpResponse]
Sleeper = Callable[[float], None]


def _default_transport() -> Transport:
    # Looked up at construction time so tests can patch urllib_transport.
    return urllib_transport


def urllib_transport(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    timeout: float,
) -> HttpResponse:
    request = urllib.request.Request(
        url,
        data=body if method != "GET" else None,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return HttpResponse(
                status=getattr(response, "status", 200),
                body=response.read(),
                headers={key: value for key, value in response.headers.items()},
                url=response.geturl(),
            )
    except urllib.error.HTTPError as exc:
        payload = exc.read() if exc.fp else b""
        hdrs = {key: value for key, value in exc.headers.items()} if exc.headers else {}
        return HttpResponse(status=exc.code, body=payload, headers=hdrs, url=url, error=str(exc.reason))
    except Exception as exc:
        return HttpResponse(status=0, body=b"", headers={}, url=url, error=f"{type(exc).__name__}: {exc}")


def read_json(response: HttpResponse) -> object:
    if response.status == 304:
        return None
    if not response.ok:
        detail = response.error or ""
        if response.body:
            detail = response.body.decode("utf-8", "replace")[:180]
        raise RemoteError(f"http {response.status} {detail}".strip())
    if not response.body or not response.body.strip():
        raise RemoteError("empty body")
    raw = response.body
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RemoteError(f"invalid json: {exc}") from exc


def read_xml(response: HttpResponse) -> bytes:
    if response.status == 304:
        return b""
    if not response.ok:
        detail = response.error or ""
        if response.body:
            detail = response.body.decode("utf-8", "replace")[:180]
        raise RemoteError(f"http {response.status} {detail}".strip())
    if not response.body or not response.body.strip():
        raise RemoteError("empty body")
    raw = response.body
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return raw


@dataclass
class HttpClient:
    transport: Transport = field(default_factory=_default_transport)
    sleeper: Sleeper = field(default=time.sleep)
    timeout: float = 20.0
    attempts: int = 3
    base_headers: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_HEADERS))

    def get(self, url: str, headers: dict[str, str] | None = None) -> HttpResponse:
        merged = {**self.base_headers, **(headers or {})}
        last: HttpResponse | None = None
        for attempt in range(self.attempts):
            response = self.transport("GET", url, merged, None, self.timeout)
            last = response
            if response.status == 304 or response.ok:
                return response
            # 429 and other 4xx (except the retry set) wait until the next hour.
            if response.status == 429 or (response.status and response.status not in RETRY_STATUS):
                return response
            if attempt < self.attempts - 1:
                self.sleeper(0.25 * (2**attempt) + random.random() * 0.25)
        assert last is not None
        return last

    def post(self, url: str, body: bytes, headers: dict[str, str] | None = None) -> HttpResponse:
        merged = {**self.base_headers, **(headers or {})}
        return self.transport("POST", url, merged, body, self.timeout)
