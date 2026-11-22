"""Labelled comparison pairs and their evaluation (evaluation/reconciliation.py).

Scanned renderings are replaced by digital PDFs here, so no OCR runs and the
app's comparison code should score perfectly on every pair it can detect.
"""

import json
import random
from pathlib import Path

import pytest

from evaluation import reconciliation
from evaluation.reconciliation import (
    PDF_CHANGES,
    SHEET_CHANGES,
    change_invoice,
    evaluate_pairs,
    generate_pairs,
    pdf_pdf_expectation,
    render_summary,
    sheet_rows,
    write_reconciliation,
)
from tests.support.fakes import FakeInvoiceLLM, sample_invoice


@pytest.fixture
def pairs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(
        reconciliation,
        "render_scan",
        lambda data, locale, rng: reconciliation.render_pdf(data, locale),
    )
    out = tmp_path / "pairs"
    generate_pairs(out, seed=5, repeats=1)
    return out


def labels(folder: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(folder.glob("*/label.json"))]


def test_every_change_is_generated_for_both_kinds(pairs: Path) -> None:
    found = labels(pairs)

    assert {lab["change"] for lab in found if lab["kind"] == "pdf_pdf"} == set(
        PDF_CHANGES
    )
    assert {lab["change"] for lab in found if lab["kind"] == "pdf_excel"} == set(
        SHEET_CHANGES
    )
    assert len(found) == len(PDF_CHANGES) * 2 + len(SHEET_CHANGES) * 4
    for label in found:
        assert (pairs / label["pair_id"] / label["left"]).exists()
        assert (pairs / label["pair_id"] / label["right"]).exists()


def test_generation_is_deterministic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        reconciliation,
        "render_scan",
        lambda data, locale, rng: reconciliation.render_pdf(data, locale),
    )
    generate_pairs(tmp_path / "a", seed=9, repeats=1)
    generate_pairs(tmp_path / "b", seed=9, repeats=1)

    assert labels(tmp_path / "a") == labels(tmp_path / "b")


def test_digital_pairs_are_scored_perfectly_except_where_undetectable(
    pairs: Path,
) -> None:
    results = evaluate_pairs(pairs, log=lambda _: None)

    rows = results["pdf_excel"]["rows"]["all"]
    assert (rows["fp"], rows["fn"]) == (0, 0)
    invoice = results["pdf_pdf"]["invoice_comparison"]
    assert invoice["pairs"]["accuracy"] == 100.0
    assert invoice["fields"]["accuracy"] == 100.0
    assert invoice["line_pairing"]["recall"] == 100.0
    assert invoice["line_differences"]["accuracy"] == 100.0
    # A row missing from the sheet cannot be found by checking sheet rows.
    assert results["by_change"]["pdf_excel:row_missing"]["detected"] == 0
    # Reordered lines are the same invoice but different text.
    assert results["by_change"]["pdf_pdf:lines_reordered"]["detected"] == 0
    assert results["pdf_pdf"]["text_diff"]["all"]["fp"] >= 1


def test_results_are_written_as_json_and_markdown(pairs: Path, tmp_path: Path) -> None:
    results = evaluate_pairs(pairs, log=lambda _: None)

    summary = write_reconciliation(results, tmp_path / "results")

    assert json.loads(
        (tmp_path / "results" / "reconciliation_results.json").read_text()
    )
    text = summary.read_text()
    assert text == render_summary(results)
    assert "## PDF vs Excel" in text and "## PDF vs PDF" in text


def test_claude_mode_extracts_both_sides_of_pdf_pairs(pairs: Path) -> None:
    llm = FakeInvoiceLLM(sample_invoice())

    results = evaluate_pairs(pairs, extraction="claude", llm=llm, log=lambda _: None)

    assert results["extraction"] == "claude"
    assert len(llm.texts) == 2 * results["pdf_pdf"]["pairs"]


# ------------------------------------------------------------------ labels


def test_changed_quantity_changes_the_line_and_the_totals() -> None:
    base = sample_invoice()
    right, origin = change_invoice(random.Random(1), base, "quantity_changed")

    expected = pdf_pdf_expectation(base, right, origin)

    assert expected["differ"] is True
    assert {"subtotal", "total_amount"} <= set(expected["different_fields"])
    assert expected["different_lines"] == [[1, 1]]


@pytest.mark.parametrize(
    ("change", "pairs"),
    [("identical", [[1, 1], [2, 2]]), ("lines_reordered", [[2, 1], [1, 2]])],
)
def test_unchanged_content_is_labelled_as_not_different(
    change: str, pairs: list[list[int]]
) -> None:
    lines = sample_invoice().line_items
    other = lines[0].model_copy(update={"description": "Travel", "product_code": "T-1"})
    base = sample_invoice(line_items=[lines[0], other])
    right, origin = change_invoice(random.Random(2), base, change)

    expected = pdf_pdf_expectation(base, right, origin)

    assert expected["differ"] is False
    assert expected["line_pairs"] == pairs


def test_added_and_removed_lines_are_unpaired() -> None:
    base = sample_invoice()
    added, origin = change_invoice(random.Random(3), base, "line_added")
    removed, origin_removed = change_invoice(random.Random(3), base, "line_removed")

    assert pdf_pdf_expectation(base, added, origin)["right_only"] == [2]
    assert pdf_pdf_expectation(base, removed, origin_removed)["left_only"] == [1]


def test_unknown_changes_are_rejected() -> None:
    with pytest.raises(ValueError):
        change_invoice(random.Random(), sample_invoice(), "teleported")
    with pytest.raises(ValueError):
        sheet_rows(random.Random(), sample_invoice(), "teleported")


def test_a_missing_sheet_row_leaves_every_remaining_row_agreeing() -> None:
    rows, agrees = sheet_rows(random.Random(4), sample_invoice(), "row_missing")

    assert rows == [] and agrees == []
