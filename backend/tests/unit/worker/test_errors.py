"""Job failures are stored as plain-language messages, without internals."""

import anthropic
import httpx2 as httpx
import pytest

from app.extraction.document_text import DocumentTooLargeError, EmptyDocumentError
from app.extraction.llm import (
    MISSING_CREDENTIALS,
    ExtractionConfigurationError,
    ExtractionIncompleteError,
    ExtractionRefusedError,
)
from app.processing.processor import DocumentNotProcessableError
from app.worker.errors import describe_failure, failure_message

REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def api_error(status: int, message: str) -> anthropic.APIStatusError:
    body = {"type": "error", "error": {"type": "x", "message": message}}
    response = httpx.Response(status, request=REQUEST, json=body)
    classes = {
        400: anthropic.BadRequestError,
        401: anthropic.AuthenticationError,
        403: anthropic.PermissionDeniedError,
        404: anthropic.NotFoundError,
        429: anthropic.RateLimitError,
        500: anthropic.InternalServerError,
    }
    cls = classes.get(status, anthropic.APIStatusError)
    return cls(f"Error code: {status} - {body}", response=response, body=body)


def test_low_credit_balance() -> None:
    error = api_error(
        400,
        "Your credit balance is too low to access the Anthropic API. Please go "
        "to Plans & Billing to upgrade or purchase credits.",
    )

    message = failure_message(error, will_retry=False)

    assert message == (
        "Invoice extraction is unavailable right now: the AI service account "
        "has run out of credits. Please contact your administrator. Once "
        "credits are added, click Retry."
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (api_error(401, "invalid x-api-key"), "rejected the API key"),
        (api_error(403, "no access"), "doesn't have access"),
        (api_error(404, "model: nope"), "model wasn't found"),
        (api_error(429, "rate limited"), "busy right now"),
        (api_error(500, "boom"), "temporarily unavailable"),
        (api_error(529, "overloaded"), "temporarily unavailable"),
        (api_error(400, "messages: bad"), "couldn't process this document"),
        (anthropic.APIConnectionError(request=REQUEST), "Couldn't reach"),
        (anthropic.APITimeoutError(request=REQUEST), "Couldn't reach"),
        (ExtractionConfigurationError(MISSING_CREDENTIALS), "isn't set up"),
        (ExtractionRefusedError("category: cyber"), "declined"),
        (ExtractionIncompleteError("cut off"), "couldn't be read completely"),
        (DocumentTooLargeError("too big"), "too much text"),
        (EmptyDocumentError("empty"), "No readable text"),
        (FileNotFoundError("/srv/storage/x.pdf"), "missing from storage"),
        (DocumentNotProcessableError("Invoice extraction is disabled."), "disabled"),
        (DocumentNotProcessableError("Document 1 not found."), "no longer exists"),
        (RuntimeError("Traceback: segfault in libtesseract"), "Something went wrong"),
    ],
)
def test_failures_are_described_without_internals(
    error: BaseException, expected: str
) -> None:
    message = describe_failure(error)

    assert expected in message
    for internal in ("Error code", "request_id", "/srv/", "Traceback", "ANTHROPIC"):
        assert internal not in message


def test_says_what_happens_next() -> None:
    error = RuntimeError("ocr crashed")

    assert failure_message(error, will_retry=True).endswith(
        "We'll try again automatically."
    )
    assert failure_message(error, will_retry=False).endswith(
        "Click Retry to try again."
    )
