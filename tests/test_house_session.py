import pytest
import requests

from gov_trades.house.session import FetchError, ThrottledSession


class _Resp:
    def __init__(self, status, content=b""):
        self.status_code = status
        self.content = content


class _FakeSession:
    def __init__(self, responses):
        self.headers = {}
        self.responses = list(responses)
        self.calls = []

    def get(self, url, timeout):
        self.calls.append((url, timeout))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class _Clock:
    def __init__(self):
        self.now = 100.0
        self.slept = []

    def monotonic(self):
        return self.now

    def sleep(self, secs):
        self.slept.append(secs)
        self.now += secs


def _make(settings, responses, clock):
    fake = _FakeSession(responses)
    ts = ThrottledSession(
        settings, session=fake, sleep=clock.sleep, monotonic=clock.monotonic, rand=lambda: 0.5
    )
    return ts, fake


def test_headers_and_body(settings):
    clock = _Clock()
    ts, fake = _make(settings, [_Resp(200, b"zip")], clock)
    assert ts.get("http://x/a") == b"zip"
    assert "test@example.invalid" in fake.headers["User-Agent"]
    assert fake.headers["From"] == "test@example.invalid"
    assert fake.calls == [("http://x/a", 5.0)]


def test_throttle_spaces_requests(settings):
    clock = _Clock()
    ts, _ = _make(settings, [_Resp(200), _Resp(200), _Resp(200)], clock)
    ts.get("http://x/1")
    assert clock.slept == []  # first request is immediate
    ts.get("http://x/2")
    assert clock.slept == [pytest.approx(2.25)]  # delay 2.0 + jitter 0.5*0.5
    clock.now += 10  # long gap: no sleep needed
    ts.get("http://x/3")
    assert len(clock.slept) == 1


def test_non_200_raises(settings):
    ts, _ = _make(settings, [_Resp(404)], _Clock())
    with pytest.raises(FetchError) as ei:
        ts.get("http://x/missing")
    assert ei.value.status == 404


def test_network_error_wrapped(settings):
    ts, _ = _make(settings, [requests.ConnectionError("boom")], _Clock())
    with pytest.raises(FetchError) as ei:
        ts.get("http://x/down")
    assert ei.value.status is None
    assert "boom" in str(ei.value)
