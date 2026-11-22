"""Partial labels, flagging and the third-party conversion."""

from pathlib import Path
from typing import Any

from evaluation.convert_third_party import convert, convert_label
from evaluation.evaluate_extraction import Case, CaseResult, load_dataset
from evaluation.evaluate_validation import Expectation, run, summarise
from evaluation.metrics import score_invoice
from evaluation.metrics import summarise as summarise_extraction
from tests.support.fakes import sample_invoice


def source_label(**expected: Any) -> dict[str, Any]:
    base = {
        "vendor_name": "Brightware Software Inc",
        "invoice_number": "BW-1",
        "invoice_date": "2024-04-18",
        "due_date": None,
        "currency": "USD",
        "subtotal": 100.0,
        "tax": 10.0,
        "total": 110.0,
        "document_type": "invoice",
        "lines": [{"description": "Hosting", "amount": 100.0, "alt_amounts": []}],
        "should_flag": False,
        "references": [],
        "scored": ["vendor_name", "invoice_number", "total", "line_items", "flag"],
        "secondary": None,
        "notes": "",
    }
    return {
        "doc_id": "bw-1",
        "file": "documents/bw-1.pdf",
        "category": "clean",
        "currency": "USD",
        "expected": base | expected,
    }


def test_only_labelled_fields_are_scored() -> None:
    expected = sample_invoice()
    actual = sample_invoice(invoice_number="WRONG", payment_terms="invented")

    full = score_invoice(expected, actual)
    partial = score_invoice(expected, actual, frozenset({"total_amount"}))

    assert not full.fields_ok
    assert [f.field for f in partial.fields] == ["total_amount"]
    assert partial.exact  # unlabelled fields are neither right nor wrong
    assert (partial.lines_expected, partial.line_fields) == (0, [])


def test_only_labelled_line_fields_are_scored() -> None:
    expected = sample_invoice()
    actual = sample_invoice()

    score = score_invoice(expected, actual, frozenset({"line.amount"}))

    assert score.lines_expected == len(expected.line_items)
    assert {f.field for f in score.line_fields} == {"line.amount"}


def test_converted_label_maps_fields_and_scoring() -> None:
    label = convert_label(source_label())

    assert label["format"] == "clean"
    assert label["should_flag"] is False
    assert label["scored_fields"] == [
        "vendor.name",
        "invoice_number",
        "total_amount",
        "line.description",
        "line.amount",
    ]
    expected = label["expected"]
    assert expected["vendor"]["name"] == "Brightware Software Inc"
    assert (expected["tax_amount"], expected["total_amount"]) == (10.0, 110.0)
    assert expected["line_items"][0]["amount"] == 100.0


def test_a_statement_only_scores_is_invoice() -> None:
    label = convert_label(
        source_label(document_type="other", scored=["vendor_name", "document_type"])
    )

    assert label["scored_fields"] == ["is_invoice"]
    assert label["expected"]["is_invoice"] is False
    assert label["expected"]["vendor"]["name"] is None


def test_convert_writes_the_harness_layout(tmp_path: Path) -> None:
    import json

    source = tmp_path / "src"
    (source / "documents").mkdir(parents=True)
    (source / "ground_truth").mkdir()
    (source / "documents" / "bw-1.pdf").write_bytes(b"%PDF-1.7")
    (source / "ground_truth" / "bw-1.json").write_text(json.dumps(source_label()))

    assert convert(source, tmp_path / "out") == 1

    (case,) = load_dataset(tmp_path / "out" / "dataset")
    assert case.name == "bw-1" and case.should_flag is False
    assert case.scored_fields and "total_amount" in case.scored_fields
    assert (tmp_path / "out" / "validation_expectations" / "bw-1.json").exists()


def _case(name: str, should_flag: bool, **overrides: object) -> Case:
    return Case(
        name, Path("x"), "clean", "us", sample_invoice(**overrides), None, should_flag
    )


def test_an_invoice_that_should_be_flagged_needs_any_failed_check() -> None:
    inconsistent = _case("bad", True, total_amount=999999.0)
    consistent = _case("ok", True)
    flag = Expectation("x", (), (), should_flag=True)

    summary = summarise(run([inconsistent, consistent], {"bad": flag, "ok": flag}))

    assert summary["classification"]["true_positives"] == 1
    assert summary["classification"]["false_negatives"] == 1
    assert summary["false_alarm_rate"] == 0.0  # any failure is acceptable


def test_extraction_flagging_metrics() -> None:
    def result(should_flag: bool, checks_pass: bool) -> CaseResult:
        score = score_invoice(sample_invoice(), sample_invoice())
        case = _case(f"{should_flag}-{checks_pass}", should_flag)
        return CaseResult(case, score, None, 0.0, 0.0, checks_pass=checks_pass)

    flagging = summarise_extraction(
        [
            result(True, False),
            result(True, True),
            result(False, False),
            result(False, True),
        ]
    )["flagging"]

    assert flagging["should_flag"] == 2
    assert (flagging["precision"], flagging["recall"]) == (50.0, 50.0)
    assert summarise_extraction([])["flagging"] is None
