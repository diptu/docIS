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
