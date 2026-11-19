import pytest

from app.processing.ocr import OcrError
from app.processing.processor import DocumentNotProcessableError
from app.worker.retry import is_retryable, retry_delay


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        (OcrError("timeout"), True),
        (ConnectionError("db restarting"), True),
        (RuntimeError("unexpected"), True),
        (FileNotFoundError("original missing"), False),
        (DocumentNotProcessableError("no extractor"), False),
    ],
)
def test_is_retryable(error: Exception, retryable: bool) -> None:
    assert is_retryable(error) is retryable


def test_retry_delay_doubles_and_is_capped() -> None:
    delays = [retry_delay(n, 30, 900, jitter=0) for n in range(1, 8)]

    assert delays == [30, 60, 120, 240, 480, 900, 900]


def test_retry_delay_jitter_stays_in_bounds() -> None:
    for _ in range(200):
        assert 24 <= retry_delay(1, 30, 900) <= 36


def test_retry_delay_zero_base() -> None:
    assert retry_delay(5, 0, 900) == 0


def _api_error(status: int) -> Exception:
    import anthropic
    import httpx2

    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(status, request=request)
    return anthropic.APIStatusError("error", response=response, body=None)


@pytest.mark.parametrize(
    ("status", "retryable"),
    [
        (429, True),
        (500, True),
        (529, True),
        (408, True),
        (400, False),
        (401, False),
        (403, False),
        (404, False),
    ],
)
def test_claude_api_errors(status: int, retryable: bool) -> None:
    assert is_retryable(_api_error(status)) is retryable


def test_claude_connection_errors_are_retried() -> None:
    import anthropic
    import httpx2

    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    assert is_retryable(anthropic.APIConnectionError(request=request)) is True
    assert is_retryable(anthropic.APITimeoutError(request=request)) is True


def test_extraction_and_configuration_errors_are_permanent() -> None:
    import anthropic

    from app.extraction.document_text import DocumentTooLargeError, EmptyDocumentError
    from app.extraction.llm import ExtractionIncompleteError, ExtractionRefusedError

    for error in (
        ExtractionRefusedError("declined"),
        ExtractionIncompleteError("cut off"),
        DocumentTooLargeError("too big"),
        EmptyDocumentError("empty"),
        anthropic.AnthropicError("api_key client option must be set"),
    ):
        assert is_retryable(error) is False
