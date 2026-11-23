"""
Where evaluations read their inputs and write their results.

    results/extraction_results.json   last extraction run: metrics + every case
    results/validation_results.json   last validation run: metrics + misses
    results/evaluation_summary.json   headline figures of both, rebuilt after each run
    results/evaluation_report.html    readable report of both, rebuilt after each run
    results/ocr_preprocessing.json    OCR on poor-quality images (evaluation ocr)
"""

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent
DATASET_DIR = ROOT / "dataset"
EXPECTATIONS_DIR = ROOT / "validation_expectations"
RESULTS_DIR = ROOT / "results"

EXTRACTION_RESULTS = "extraction_results.json"
VALIDATION_RESULTS = "validation_results.json"
EVALUATION_SUMMARY = "evaluation_summary.json"
EVALUATION_REPORT = "evaluation_report.html"
OCR_RESULTS = "ocr_preprocessing.json"


def write_json(path: Path, content: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content, indent=2, ensure_ascii=False) + "\n")


def _read(path: Path) -> dict[str, Any] | None:
    try:
        content = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return content if isinstance(content, dict) else None


def _extraction_headline(run: dict[str, Any]) -> dict[str, Any]:
    s = run["summary"]
    return {
        "run_at": run["run_at"],
        "model": run["model"],
        "documents": s["cases"],
        "success_rate": s["reliability"]["success_rate"],
        "documents_fully_correct": s["documents_exact"],
        "field_accuracy": s["fields"]["accuracy"],
        "hallucination_rate": s["fields"]["hallucination_rate"],
        "line_items_f1": s["line_items"]["f1"],
        "amount_checks_pass": s["amount_checks_pass"],
        "median_seconds_per_document": s["timing_seconds"]["total"]["median"],
        "estimated_cost_usd": s["estimated_cost_usd"],
    }


def _validation_headline(run: dict[str, Any]) -> dict[str, Any]:
    s = run["summary"]
    return {
        "run_at": run["run_at"],
        "invoices": s["invoices"],
        "injected_errors": s["injected_errors"],
        "detection_rate": s["detection_rate"],
        "false_alarm_rate": s["false_alarm_rate"],
        "precision": s["classification"]["precision"],
        "recall": s["classification"]["recall"],
        "f1": s["classification"]["f1"],
    }


def write_summary(results_dir: Path) -> dict[str, Any]:
    """Combine the latest extraction and validation runs (either may be
    missing) into ``evaluation_summary.json`` and the HTML report."""
    extraction = _read(results_dir / EXTRACTION_RESULTS)
    validation = _read(results_dir / VALIDATION_RESULTS)
    summary = {
        "extraction": _extraction_headline(extraction) if extraction else None,
        "validation": _validation_headline(validation) if validation else None,
    }
    write_json(results_dir / EVALUATION_SUMMARY, summary)
    from evaluation.html_report import write_report  # imports this module

    write_report(results_dir)
    return summary
