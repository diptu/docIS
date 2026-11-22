"""Token buckets, client identification and route classification
(app/api/rate_limit.py). The middleware itself is tested in
tests/api/test_rate_limit.py."""

import pytest

from app.api import rate_limit
from app.api.rate_limit import TokenBucketLimiter, client_key, starts_processing


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


def test_allows_a_burst_up_to_the_limit_then_refuses(clock: Clock) -> None:
    limiter = TokenBucketLimiter(clock)

    assert [limiter.hit("a", 3) for _ in range(3)] == [0.0, 0.0, 0.0]
    # Empty: 3/min refills one token every 20 s.
    assert limiter.hit("a", 3) == pytest.approx(20.0)


def test_refills_continuously(clock: Clock) -> None:
    limiter = TokenBucketLimiter(clock)
    for _ in range(3):
        limiter.hit("a", 3)

    clock.now += 10
    assert limiter.hit("a", 3) == pytest.approx(10.0)  # half a token so far
    clock.now += 10
    assert limiter.hit("a", 3) == 0.0


def test_refused_requests_do_not_spend_tokens(clock: Clock) -> None:
    limiter = TokenBucketLimiter(clock)
    limiter.hit("a", 1)
    for _ in range(5):
        assert limiter.hit("a", 1) > 0

    clock.now += 60
    assert limiter.hit("a", 1) == 0.0


def test_never_holds_more_than_the_limit(clock: Clock) -> None:
    limiter = TokenBucketLimiter(clock)
    limiter.hit("a", 2)
    clock.now += 3600  # idle for an hour

    assert [limiter.hit("a", 2) for _ in range(3)][-1] > 0


def test_keys_are_independent(clock: Clock) -> None:
    limiter = TokenBucketLimiter(clock)
    limiter.hit("a", 1)

    assert limiter.hit("a", 1) > 0
    assert limiter.hit("b", 1) == 0.0


def test_reset_forgets_everyone(clock: Clock) -> None:
    limiter = TokenBucketLimiter(clock)
    limiter.hit("a", 1)
    limiter.reset()

    assert len(limiter) == 0
    assert limiter.hit("a", 1) == 0.0


def test_idle_buckets_are_pruned(clock: Clock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rate_limit, "PRUNE_EVERY", 2)
    limiter = TokenBucketLimiter(clock)
    limiter.hit("old", 10)
    clock.now += 61
    limiter.hit("new", 10)  # second hit: prunes

    assert len(limiter) == 1


def test_a_flood_of_clients_cannot_grow_memory_without_bound(
    clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rate_limit, "MAX_KEYS", 5)
    limiter = TokenBucketLimiter(clock)
    for n in range(20):
        limiter.hit(f"client-{n}", 10)

    assert len(limiter) < 5


def _scope(client: str = "10.0.0.1", forwarded: str | None = None) -> dict:
    headers = [] if forwarded is None else [(b"x-forwarded-for", forwarded.encode())]
    return {"client": (client, 1234), "headers": headers}


def test_client_is_the_socket_peer_without_trusted_proxies() -> None:
    assert client_key(_scope(forwarded="1.2.3.4"), 0) == "10.0.0.1"


def test_client_is_counted_from_the_right_of_x_forwarded_for() -> None:
    # The leftmost entry is whatever the client claimed; only proxy-appended
    # entries are trusted.
    scope = _scope(forwarded="6.6.6.6, 203.0.113.7, 198.51.100.2")

    assert client_key(scope, 1) == "198.51.100.2"
    assert client_key(scope, 2) == "203.0.113.7"


def test_short_x_forwarded_for_falls_back_to_the_socket_peer() -> None:
    assert client_key(_scope(forwarded="203.0.113.7"), 2) == "10.0.0.1"


def test_missing_client_is_grouped_as_unknown() -> None:
    assert client_key({"headers": []}, 0) == "unknown"


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("POST", "/documents", True),
        ("POST", "/documents/", True),
        ("POST", "/documents/0b3d/processing/retry", True),
        ("GET", "/documents", False),
        ("POST", "/documents/delete", False),
        ("GET", "/documents/0b3d/processing", False),
        ("PUT", "/documents/0b3d/invoice", False),
    ],
)
def test_only_uploads_and_retries_start_processing(
    method: str, path: str, expected: bool
) -> None:
    assert starts_processing(method, path) is expected
