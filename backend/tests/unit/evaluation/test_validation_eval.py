"""The validation evaluation: expectations, injected errors and metrics."""

import json
from pathlib import Path

import pytest

from evaluation.evaluate_extraction import Case
from evaluation.evaluate_validation import (
    Expectation,
    apply,
    expectation_for,
    load_expectations,
    run,
    summarise,
)
from evaluation.metrics import binary_classification
from tests.support.fakes import sample_invoice


def _case(name: str = "INV-1", **overrides: object) -> Case:
    return Case(name, Path("x"), "pdf", "us", sample_invoice(**overrides))


def test_generated_expectations_are_met_by_the_checks() -> None:
    case = _case()
    expectation = expectation_for(case.expected)

    summary = summarise(run([case], {case.name: expectation}))

    assert expectation.expected_failed_checks == ()
    assert {e.name for e in expectation.injected_errors} >= {
        "subtotal",
        "total_amount",
        "amount_due_over_total",
    }
    assert summary["scenarios_correct"] == 100.0
    assert summary["detection_rate"] == 100.0
    assert summary["false_alarm_rate"] == 0.0
    assert summary["per_check"]["amount_due"]["recall"] == 100.0


def test_injected_errors_need_their_fields() -> None:
    data = sample_invoice(total_amount=None, amount_due=None)

    names = {e.name for e in expectation_for(data).injected_errors}

    assert not names & {
        "subtotal",
        "tax_amount",
        "total_amount",
        "amount_due_over_total",
    }


def test_expectations_round_trip_and_reject_unknown_checks(tmp_path: Path) -> None:
    expectation = expectation_for(sample_invoice())
    assert Expectation.from_json(expectation.to_json()) == expectation

    bad = expectation.to_json() | {"expected_failed_checks": ["no_such_check"]}
    with pytest.raises(ValueError, match="no_such_check"):
        Expectation.from_json(bad)


def test_a_wrong_expectation_is_reported_as_missed_and_false_alarm() -> None:
    case = _case()
    expectation = Expectation.from_json(
        {
            "invoice_number": "INV-1",
            # Claims the labelled invoice is inconsistent: a miss.
            "expected_failed_checks": ["total"],
            # A harmless change that cannot make the total check fail.
            "injected_errors": [
                {"name": "noop", "set": {"currency": "EUR"}, "must_fail": ["total"]}
            ],
        }
    )

    summary = summarise(run([case], {"INV-1": expectation}))

    assert summary["detection_rate"] == 0.0
    assert summary["per_check"]["total"] == {
        "expected_failures": 2,
        "caught": 0,
        "recall": 0.0,
        "false_alarms": 0,
    }


def test_cases_without_expectations_are_listed() -> None:
    summary = summarise(run([_case("A"), _case("B")], {}))

    assert summary["invoices"] == 0
    assert summary["invoices_without_expectations"] == ["A", "B"]


def test_apply_sets_dotted_paths() -> None:
    data = sample_invoice()

    changed = apply(data, {"line_items.0.amount": 1.23, "vendor.name": "X"})

    assert changed.line_items[0].amount == 1.23 and changed.vendor.name == "X"
    assert data.line_items[0].amount != 1.23  # the original is untouched
    with pytest.raises(KeyError):
        apply(data, {"no_such_field": 1})


def test_load_expectations_by_invoice_number(tmp_path: Path) -> None:
    expectation = expectation_for(sample_invoice())
    (tmp_path / "INV-1.json").write_text(json.dumps(expectation.to_json()))

    assert load_expectations(tmp_path) == {"INV-1": expectation}


def test_binary_classification() -> None:
    assert binary_classification(tp=8, fp=2, fn=2, tn=8) == {
        "true_positives": 8,
        "false_positives": 2,
        "false_negatives": 2,
        "true_negatives": 8,
        "precision": 80.0,
        "recall": 80.0,
        "f1": 80.0,
        "specificity": 80.0,
    }
