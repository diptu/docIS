"""
Retry policy: which failures are worth retrying, and when.
"""

import random

from app.processing.processor import DocumentNotProcessableError

# Failures that another attempt cannot fix: the document is gone or has no
# extractor, or its stored original is missing.
PERMANENT_ERRORS: tuple[type[BaseException], ...] = (
    DocumentNotProcessableError,
    FileNotFoundError,
)


def is_retryable(error: BaseException) -> bool:
    """True unless the error is known to be permanent.

    Unknown errors are retried: transient causes (database restarts, OCR
    timeouts, resource pressure) are common, and the attempt limit bounds
    the cost of retrying a deterministic failure.
    """
    return not isinstance(error, PERMANENT_ERRORS)


def retry_delay(
    attempt: int,
    base_seconds: float,
    max_seconds: float,
    jitter: float = 0.2,
) -> float:
    """Seconds to wait after failed attempt number ``attempt`` (1-based).

    Exponential backoff, ``base * 2^(attempt-1)`` capped at ``max_seconds``,
    randomised by +/-``jitter`` so failed jobs do not all retry in lockstep.
    """
    delay = min(max_seconds, base_seconds * 2.0 ** max(attempt - 1, 0))
    if jitter:
        delay *= random.uniform(1 - jitter, 1 + jitter)
    return max(delay, 0.0)
