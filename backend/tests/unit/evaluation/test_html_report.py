"""The HTML report: built from the results files, escaped, rebuilt per run."""

from pathlib import Path

from evaluation.evaluate_extraction import Case, CaseResult, write_results
from evaluation.evaluate_validation import ValidationRun
from evaluation.evaluate_validation import write_results as write_val
from evaluation.html_report import render, write_report
from evaluation.metrics import score_invoice
from tests.support.fakes import sample_invoice


def _result(name: str) -> CaseResult:
    expected = sample_invoice()
    actual = sample_invoice(invoice_number="WRONG")
    case = Case(name, Path("x.pdf"), "scan", "us", expected)
    return CaseResult(
        case, score_invoice(expected, actual), None, 0.1, 1.0, checks_pass=True
    )


def test_every_run_rebuilds_the_report(tmp_path: Path) -> None:
    write_results([_result("INV-1")], tmp_path, "claude", "dataset")

    html = (tmp_path / "evaluation_report.html").read_text()

    assert "<h1>Evaluation report</h1>" in html
    assert "Accuracy per field" in html and "Cases with mistakes" in html
    assert "INV-1" in html and "WRONG" in html
    assert "Harness check" not in html  # a real model run
    assert 'id="validation"' not in html  # not run yet

    write_val(ValidationRun([], []), tmp_path, "dataset", "expectations")
    assert 'id="validation"' in (tmp_path / "evaluation_report.html").read_text()


def test_dataset_text_is_escaped(tmp_path: Path) -> None:
    write_results([_result('<img src=x onerror="alert(1)">')], tmp_path, "oracle", "d")

    html = render(tmp_path)

    assert "<img src=x" not in html
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in html
    assert "Harness check" in html  # oracle runs are labelled as such


def test_report_without_results(tmp_path: Path) -> None:
    path = write_report(tmp_path / "results")

    assert "No results yet" in path.read_text()
