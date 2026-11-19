"""
Retry policy: which failures are worth retrying, and when.
"""

import random

import anthropic

from app.extraction.document_text import DocumentTooLargeError, EmptyDocumentError
from app.extraction.llm import ExtractionError
from app.processing.processor import DocumentNotProcessableError

# Failures that another attempt cannot fix: the document is gone or has no
# extractor, its stored original is missing, or the extraction input/output
# is unusable (too large, empty, refused, cut off).
PERMANENT_ERRORS: tuple[type[BaseException], ...] = (
    DocumentNotProcessableError,
    FileNotFoundError,
    DocumentTooLargeError,
    EmptyDocumentError,
    ExtractionError,
)
# Claude API statuses worth retrying: timeout, conflict, rate limit, and all
# server-side errors (5xx, including 529 overloaded).
RETRYABLE_API_STATUSES = {408, 409, 429}


def is_retryable(error: BaseException) -> bool:
    """True unless the error is known to be permanent.

    Unknown errors are retried: transient causes (database restarts, OCR
    timeouts, resource pressure) are common, and the attempt limit bounds
    the cost of retrying a deterministic failure.
    """
    if isinstance(error, PERMANENT_ERRORS):
        return False
    if isinstance(error, anthropic.APIStatusError):
        # Other 4xx (bad request, authentication, permission, not found)
        # need a code or configuration fix, not another attempt.
        return error.status_code >= 500 or error.status_code in RETRYABLE_API_STATUSES
    if isinstance(error, anthropic.APIConnectionError):  # includes timeouts
        return True
    # Client-side SDK errors, e.g. missing credentials, are configuration.
    return not isinstance(error, anthropic.AnthropicError)


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
