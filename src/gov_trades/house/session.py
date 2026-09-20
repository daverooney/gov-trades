"""Polite HTTP session for the Clerk's disclosure site.

Plain ``requests``: no handshake, no impersonation. Every request carries a
contact User-Agent and is spaced by a delay plus jitter. The clock and sleep
are injectable so the throttle is testable without waiting.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable

import requests

from ..config import Settings


class FetchError(RuntimeError):
    def __init__(self, url: str, status: int | None, detail: str = "") -> None:
        self.url = url
        self.status = status
        super().__init__(f"{url}: {detail or status}")


class ThrottledSession:
    def __init__(
        self,
        settings: Settings,
        *,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        rand: Callable[[], float] = random.random,
    ) -> None:
        self._delay = settings.request_delay_seconds
        self._jitter = settings.request_jitter_seconds
        self._timeout = settings.request_timeout_seconds
        self._session = session or requests.Session()
        self._session.headers["User-Agent"] = settings.user_agent
        self._session.headers["From"] = settings.contact_email
        self._sleep = sleep
        self._monotonic = monotonic
        self._rand = rand
        self._next_allowed = 0.0

    def _throttle(self) -> None:
        now = self._monotonic()
        if now < self._next_allowed:
            self._sleep(self._next_allowed - now)
        self._next_allowed = self._monotonic() + self._delay + self._jitter * self._rand()

    def get(self, url: str) -> bytes:
        """GET a URL and return the body. Raises FetchError on any non-200."""
        self._throttle()
        try:
            resp = self._session.get(url, timeout=self._timeout)
        except requests.RequestException as exc:
            raise FetchError(url, None, str(exc)) from exc
        if resp.status_code != 200:
            raise FetchError(url, resp.status_code)
        return resp.content
