"""Minimal JSON-over-HTTP helper with throttling and retries (stdlib only)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

USER_AGENT = "solana-monitor/0.1"


class HttpJsonClient:
    def __init__(self, min_interval_s: float = 0.0, retries: int = 3, timeout_s=15):
        self.min_interval_s = min_interval_s
        self.retries = retries
        self.timeout_s = timeout_s
        self._last_request = 0.0

    def _throttle(self) -> None:
        wait = self.min_interval_s - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def request(
        self, url: str, payload: dict | None = None, headers: dict | None = None
    ):
        data = None if payload is None else json.dumps(payload).encode()
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            **(headers or {}),
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        for attempt in range(self.retries):
            self._throttle()
            req = urllib.request.Request(url, data=data, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    return json.load(resp)
            except urllib.error.HTTPError as exc:
                # Retry on rate limiting and server errors only.
                if exc.code != 429 and exc.code < 500 or attempt == self.retries - 1:
                    raise
            except urllib.error.URLError:
                if attempt == self.retries - 1:
                    raise
            time.sleep(2**attempt)
        raise RuntimeError("unreachable")
