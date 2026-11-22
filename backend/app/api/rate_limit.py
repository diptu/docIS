"""
Per-client request rate limiting.

Every request except the health checks spends a token from the client's
*general* bucket. Requests that start paid background work (uploading
documents, retrying processing: OCR plus a Claude call) also spend one
from a much smaller *processing* bucket. An empty bucket answers 429 with
``Retry-After`` before the request body is read.

Buckets are token buckets: ``N`` per minute allows a burst of ``N`` and then
refills continuously at ``N/60`` per second. State is kept in memory, per
API process, which matches the deployment (one API process per instance).
With several API instances each enforces its own limit, so the effective
limit is the per-process limit times the number of instances.

The client is identified by IP address. Behind reverse proxies the socket
peer is the proxy, so ``trusted_proxy_hops`` says how many proxies append
to ``X-Forwarded-For``. The client is the entry that many places from the
right. Entries further left are supplied by the client and are never
trusted. With 0 hops, the socket peer is used.
"""

import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

EXEMPT_PREFIXES = ("/health",)
UPLOAD_PATHS = frozenset({"/documents", "/documents/"})
RETRY_PATH = re.compile(r"^/documents/[^/]+/processing/retry/?$")

# Buckets idle this long are full again and can be forgotten.
IDLE_SECONDS = 60.0
PRUNE_EVERY = 1_000
MAX_KEYS = 100_000


@dataclass(frozen=True)
class RateLimits:
    enabled: bool
    per_minute: int
    processing_per_minute: int
    trusted_proxy_hops: int


class TokenBucketLimiter:
    """In-memory token buckets keyed by an arbitrary string.

    Not thread-safe; it is used from the event loop only.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        # key -> (tokens left, time of last update)
        self._buckets: dict[str, tuple[float, float]] = {}
        self._hits = 0

    def __len__(self) -> int:
        return len(self._buckets)

    def reset(self) -> None:
        """Forget every client (tests, or a configuration change)."""
        self._buckets.clear()
        self._hits = 0

    def hit(self, key: str, per_minute: int) -> float:
        """Spend one token. Returns 0 when allowed, otherwise the number of
        seconds until a token is available (nothing is spent)."""
        now = self._clock()
        rate = per_minute / 60.0
        tokens, updated = self._buckets.get(key, (float(per_minute), now))
        tokens = min(float(per_minute), tokens + (now - updated) * rate)
        if tokens >= 1.0:
            self._buckets[key] = (tokens - 1.0, now)
            self._maybe_prune(now)
            return 0.0
        self._buckets[key] = (tokens, now)
        return (1.0 - tokens) / rate

    def _maybe_prune(self, now: float) -> None:
        self._hits += 1
        if self._hits % PRUNE_EVERY and len(self._buckets) < MAX_KEYS:
            return
        self._buckets = {
            key: value
            for key, value in self._buckets.items()
            if now - value[1] < IDLE_SECONDS
        }
        if len(self._buckets) >= MAX_KEYS:
            # Flooded by distinct clients within a minute: fail open rather
            # than grow without bound.
            self._buckets.clear()


def client_key(scope: Scope, trusted_proxy_hops: int) -> str:
    """The client's IP address, as described in the module docstring."""
    if trusted_proxy_hops > 0:
        forwarded: list[str] = [
            part.strip()
            for name, value in scope.get("headers", [])
            if name == b"x-forwarded-for"
            for part in value.decode("latin-1").split(",")
            if part.strip()
        ]
        if len(forwarded) >= trusted_proxy_hops:
            return forwarded[-trusted_proxy_hops]
    client = scope.get("client")
    return client[0] if client else "unknown"


def starts_processing(method: str, path: str) -> bool:
    """Requests that queue OCR and AI extraction."""
    return method == "POST" and (path in UPLOAD_PATHS or bool(RETRY_PATH.match(path)))


def _too_many(retry_after: float, what: str) -> JSONResponse:
    seconds = max(1, math.ceil(retry_after))
    return JSONResponse(
        status_code=429,
        content={
            "detail": (
                f"Too many {what}. Please wait {seconds} second"
                f"{'' if seconds == 1 else 's'} and try again."
            )
        },
        headers={"Retry-After": str(seconds)},
    )


class RateLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        limits: Callable[[], RateLimits],
        limiter: TokenBucketLimiter,
    ) -> None:
        """``limits()`` is called per request, so the current settings apply."""
        self.app = app
        self.limits = limits
        self.limiter = limiter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"].startswith(EXEMPT_PREFIXES):
            await self.app(scope, receive, send)
            return

        limits = self.limits()
        if not limits.enabled:
            await self.app(scope, receive, send)
            return

        client = client_key(scope, limits.trusted_proxy_hops)
        if starts_processing(scope["method"], scope["path"]):
            wait = self.limiter.hit(
                f"processing:{client}", limits.processing_per_minute
            )
            if wait:
                await _too_many(wait, "uploads or retries")(scope, receive, send)
                return

        wait = self.limiter.hit(f"general:{client}", limits.per_minute)
        if wait:
            await _too_many(wait, "requests")(scope, receive, send)
            return

        await self.app(scope, receive, send)
