import datetime as dt

import pytest

from evaluation.scoring import (
    CORRECT,
    CORRECT_NULL,
    HALLUCINATED,
    MISSED,
    WRONG,
    outcome,
    score_invoice,
)
from tests.support.fakes import sample_invoice


@pytest.mark.parametrize(
    ("expected", "actual", "result"),
    [
        ("Hauptstr. 1,\n10115 Berlin", "hauptstr 1 10115 berlin", CORRECT),
        ("INV-2026/001", "inv 2026 001", CORRECT),
        ("ACME GmbH", "Globex", WRONG),
        (1487.5, 1487.504, CORRECT),
        (1487.5, 1487.6, WRONG),
        (dt.date(2026, 11, 3), dt.date(2026, 11, 3), CORRECT),
        (dt.date(2026, 11, 3), dt.date(2026, 3, 11), WRONG),  # day/month swapped
        (True, False, WRONG),
        ("x", None, MISSED),
        (None, "invented", HALLUCINATED),
        (None, None, CORRECT_NULL),
    ],
)
def test_outcome(expected: object, actual: object, result: str) -> None:
    assert outcome(expected, actual) == result


def test_perfect_extraction_is_exact() -> None:
    score = score_invoice(sample_invoice(), sample_invoice())

    assert score.exact and score.fields_ok
    assert len(score.fields) == 21
    assert score.outcomes()[CORRECT] + score.outcomes()[CORRECT_NULL] == 21


def test_mistakes_are_classified() -> None:
    actual = sample_invoice(
        total_amount=1500.0, payment_terms=None, amount_due=99.0, line_items=[]
    )

    score = score_invoice(sample_invoice(), actual)

    by_field = {f.field: f.outcome for f in score.fields}
    assert by_field["total_amount"] == WRONG
    assert by_field["payment_terms"] == MISSED
    assert by_field["amount_due"] == HALLUCINATED
    assert (score.lines_expected, score.lines_extracted, score.lines_matched) == (
        1,
        0,
        0,
    )
    assert not score.exact


def test_line_item_fields_are_scored_for_matched_lines() -> None:
    item = sample_invoice().line_items[0].model_copy(update={"quantity": 11.0})

    score = score_invoice(sample_invoice(), sample_invoice(line_items=[item]))

    assert score.fields_ok and not score.exact
    assert [f.field for f in score.line_fields if not f.ok] == ["line.quantity"]
