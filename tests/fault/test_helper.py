from __future__ import annotations

import json

import httpx
import pytest

from fault import Fault, faulty_endpoint


def test_server_error_is_a_500() -> None:
    with faulty_endpoint(Fault.SERVER_ERROR) as server:
        response = httpx.post(f"{server.url}/v1/traces", content=b"x")

    assert response.status_code == 500
    assert server.requests == ["/v1/traces"]


def test_rate_limited_carries_the_retry_after_header() -> None:
    """The header is the point. QUOTAS.md records a real Gemini 429 asking for
    40 seconds while five attempts of backoff total about 31, so a client that
    ignores it retries entirely inside the cooldown."""
    with faulty_endpoint(Fault.RATE_LIMITED, retry_after=40) as server:
        response = httpx.post(f"{server.url}/v1/chat", content=b"x")

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "40"


def test_malformed_is_a_200_carrying_nonsense() -> None:
    """The nastiest of the set: every status check passes and the failure only
    appears at parse time, somewhere else entirely."""
    with faulty_endpoint(Fault.MALFORMED) as server:
        response = httpx.post(f"{server.url}/v1/chat", content=b"x")

    assert response.status_code == 200
    with pytest.raises(json.JSONDecodeError):
        response.json()


def test_unreachable_refuses_the_connection() -> None:
    """Binds nothing at all, so this is a real connection refused rather than a
    simulated one."""
    with faulty_endpoint(Fault.UNREACHABLE) as server, pytest.raises(httpx.ConnectError):
        httpx.post(f"{server.url}/v1/traces", content=b"x", timeout=5)


def test_reset_drops_the_connection() -> None:
    with faulty_endpoint(Fault.RESET) as server, pytest.raises(httpx.HTTPError):
        httpx.post(f"{server.url}/v1/traces", content=b"x", timeout=5)


def test_hang_outlives_the_client_timeout() -> None:
    """A dependency that is slow is more dangerous than one that is down: nothing
    reports an error, and the caller simply stops."""
    with (
        faulty_endpoint(Fault.HANG, hang_seconds=5) as server,
        pytest.raises(httpx.TimeoutException),
    ):
        httpx.post(f"{server.url}/v1/traces", content=b"x", timeout=0.5)


def test_the_server_records_what_it_was_asked_for() -> None:
    """Every fault test asserts something did or did not reach the far side, so
    the helper has to be able to say."""
    with faulty_endpoint(Fault.SERVER_ERROR) as server:
        for path in ("/v1/traces", "/v1/metrics", "/v1/traces"):
            httpx.post(f"{server.url}{path}", content=b"x")

    assert server.requests == ["/v1/traces", "/v1/metrics", "/v1/traces"]
