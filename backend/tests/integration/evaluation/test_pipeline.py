"""Generator, runner, report and CLI, end to end on a small dataset."""

import asyncio
import json
import random
from pathlib import Path

import pytest

from app.extraction.validation import CheckStatus, validate_amounts
from app.processing.ocr import OcrConfig
from evaluation.__main__ import main
from evaluation.evaluate_extraction import (
    NoisyLLM,
    OracleLLM,
    load_dataset,
    render_markdown,
    run,
    write_results,
)
from evaluation.generate import FORMATS, generate, make_invoice
from evaluation.metrics import summarise
from tests.support.helpers import requires_tesseract


def test_generated_invoices_add_up() -> None:
    rng = random.Random(1)
    for number in range(50):
        data, _ = make_invoice(rng, number)
        checks = validate_amounts(data)
        assert not [c for c in checks if c.status == CheckStatus.FAILED], data


def test_generation_is_deterministic(tmp_path: Path) -> None:
    generate(tmp_path / "a" / "dataset", 4, seed=5)
    generate(tmp_path / "b" / "dataset", 4, seed=5)

    for folder in ("dataset/ground_truth", "validation_expectations"):
        labels = sorted((tmp_path / "a" / folder).glob("*.json"))
        assert len(labels) == 4
        for label in labels:
            twin = tmp_path / "b" / folder / label.name
            assert label.read_text() == twin.read_text()


def test_cases_are_named_after_their_invoice_number(tmp_path: Path) -> None:
    documents = generate(tmp_path / "dataset", 4, seed=5)

    for case in load_dataset(tmp_path / "dataset"):
        assert case.name == case.expected.invoice_number
        assert case.path in documents
        assert (tmp_path / "validation_expectations" / f"{case.name}.json").exists()


@pytest.fixture(scope="module")
def dataset(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("evaluation") / "dataset"
    generate(directory, len(FORMATS), seed=11)  # one case per format
    return directory


def test_dataset_loads_with_labels(dataset: Path) -> None:
    cases = load_dataset(dataset)

    assert sorted(case.format for case in cases) == sorted(FORMATS)
    assert all(case.expected.is_invoice for case in cases)


def test_dataset_rejects_labels_without_one_document(tmp_path: Path) -> None:
    (tmp_path / "ground_truth").mkdir()
    (tmp_path / "ground_truth" / "orphan.json").write_text("{}")

    with pytest.raises(ValueError, match="exactly one document"):
        load_dataset(tmp_path)


@requires_tesseract
async def test_oracle_scores_perfectly_on_every_format(
    dataset: Path, tmp_path: Path
) -> None:
    results = await run(
        load_dataset(dataset), OracleLLM(), OcrConfig(timeout_seconds=30)
    )

    summary = write_results(results, tmp_path, "oracle", str(dataset))

    assert summary["errors"] == []
    assert summary["documents_exact"] == 100.0
    assert summary["fields"]["hallucination_rate"] == 0.0
    assert summary["line_items"]["f1"] == 100.0
    assert set(summary["by_format"]) == set(FORMATS)
    report = render_markdown(summary, "oracle", str(dataset))
    assert "| Documents fully correct (fields + line items) | 100.0% |" in report
    saved = json.loads((tmp_path / "extraction_results.json").read_text())
    assert saved["model"] == "oracle" and saved["summary"] == summary
    assert all(case["mistakes"] == [] for case in saved["cases"])
    headline = json.loads((tmp_path / "evaluation_summary.json").read_text())
    assert headline["extraction"]["documents_fully_correct"] == 100.0
    assert headline["validation"] is None


@requires_tesseract
async def test_noisy_model_lowers_the_scores(dataset: Path) -> None:
    results = await run(
        load_dataset(dataset), NoisyLLM(rate=1.0), OcrConfig(timeout_seconds=30)
    )

    summary = summarise(results)

    assert summary["documents_exact"] == 0.0
    assert summary["fields"]["miss_rate"] > 0
    assert summary["fields"]["hallucination_rate"] > 0
    assert summary["line_items"]["recall"] < 100


async def test_model_and_content_errors_are_recorded(dataset: Path) -> None:
    class Broken(OracleLLM):
        async def extract(self, document_text: str) -> object:  # type: ignore[override]
            raise RuntimeError("API down")

    cases = [c for c in load_dataset(dataset) if c.format == "csv"]
    broken_file = dataset.parent / "broken.pdf"
    broken_file.write_bytes(b"%PDF-1.7 garbage")
    cases.append(type(cases[0])("broken", broken_file, "pdf", "us", cases[0].expected))

    results = await run(cases, Broken(), OcrConfig())

    errors = summarise(results)["errors"]
    assert errors[0] == {
        "case": cases[0].name,
        "error": "Model: RuntimeError: API down",
    }
    assert errors[1]["error"].startswith("Content: ")

    reliability = summarise(results)["reliability"]
    assert reliability == {
        "documents": 2,
        "succeeded": 0,
        "failed": 2,
        "success_rate": 0.0,
        "failure_rate": 100.0,
        "failures_by_stage": {
            "content_extraction": 1,
            "ai_extraction": 1,
            "other": 0,
        },
    }


def test_cli(dataset: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    gen = tmp_path / "gen" / "dataset"
    assert main(["generate", "--dataset", str(gen), "--count", "2"]) == 0
    assert len(list((gen / "ground_truth").glob("*.json"))) == 2
    assert len(list((tmp_path / "gen" / "validation_expectations").iterdir())) == 2

    # Claude costs money: refuses without --yes.
    assert main(["run", "--dataset", str(dataset), "--llm", "claude"]) == 2
    assert "--yes" in capsys.readouterr().out

    assert main(["run", "--dataset", str(tmp_path / "empty"), "--llm", "oracle"]) == 1

    out = tmp_path / "results"
    code = main(
        [
            "run",
            "--dataset",
            str(gen),
            "--llm",
            "oracle",
            "--out",
            str(out),
        ]
    )
    assert code == 0
    assert "## Headline" in capsys.readouterr().out
    # Expectations default to validation_expectations next to the dataset.
    assert main(["validate", "--dataset", str(gen), "--out", str(out)]) == 0
    assert "Detection rate: 100.0%" in capsys.readouterr().out
    assert sorted(p.name for p in out.iterdir()) == [
        "evaluation_report.html",
        "evaluation_summary.json",
        "extraction_results.json",
        "validation_results.json",
    ]
    headline = json.loads((out / "evaluation_summary.json").read_text())
    assert headline["extraction"] and headline["validation"]

    empty = tmp_path / "no-expectations"
    empty.mkdir()
    args = ["validate", "--dataset", str(gen), "--expectations", str(empty)]
    assert main([*args, "--out", str(tmp_path / "x")]) == 1


def test_cli_fails_loudly_when_no_case_can_be_scored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import evaluation.__main__ as cli

    class NoKey(OracleLLM):
        async def extract(self, document_text: str) -> object:  # type: ignore[override]
            raise RuntimeError("No Claude credentials are configured.")

    monkeypatch.setattr(cli, "_llm", lambda name, noise: NoKey())
    generate(tmp_path / "data", 2, seed=3, expectations=tmp_path / "expectations")

    code = main(
        [
            "run",
            "--dataset",
            str(tmp_path / "data"),
            "--llm",
            "claude",
            "--yes",
            # Never write test reports into the real evaluation/results.
            "--out",
            str(tmp_path / "results"),
        ]
    )

    assert code == 3
    output = capsys.readouterr().out
    assert "2 of 2 cases failed" in output and "No Claude credentials" in output


def _result(fmt: str, content: float, model: float, error: str | None = None) -> object:
    from evaluation.evaluate_extraction import Case, CaseResult
    from tests.support.fakes import sample_invoice

    case = Case(f"case-{fmt}-{content}", Path("x"), fmt, "us", sample_invoice())
    return CaseResult(case, None, error, content, model)


def test_success_rate_and_processing_time_statistics() -> None:
    results = [
        _result("pdf", 0.1, 1.0),
        _result("pdf", 0.3, 2.0),
        _result("scan", 2.0, 3.0),
        _result("csv", 0.05, 0.95),
        _result("pdf", 0.2, 0.0, error="Model: TimeoutError: slow"),
        _result("zip", 0.0, 0.0, error="Unsupported file type"),
    ]

    summary = summarise(results)  # type: ignore[arg-type]

    reliability = summary["reliability"]
    assert (reliability["succeeded"], reliability["failed"]) == (4, 2)
    assert (reliability["success_rate"], reliability["failure_rate"]) == (66.7, 33.3)
    assert reliability["failures_by_stage"] == {
        "content_extraction": 0,
        "ai_extraction": 1,
        "other": 1,
    }
    timing = summary["timing_seconds"]
    # Only successful documents count: totals 1.1, 2.3, 5.0, 1.0 s.
    assert timing["total"] == {"mean": 2.35, "median": 1.7, "p95": 5.0, "max": 5.0}
    assert timing["content_extraction"]["max"] == 2.0
    assert timing["ai_extraction"]["median"] == 1.5
    assert set(timing["total_by_format"]) == {"csv", "pdf", "scan"}
    assert timing["total_by_format"]["pdf"]["mean"] == 1.7


@requires_tesseract
def test_report_shows_reliability_and_timing(dataset: Path, tmp_path: Path) -> None:
    cases = load_dataset(dataset)
    results = asyncio.run(run(cases, OracleLLM(), OcrConfig()))

    summary = write_results(results, tmp_path, "oracle", str(dataset))
    report = render_markdown(summary, "oracle", str(dataset))

    assert summary["reliability"]["success_rate"] == 100.0
    assert "| Document success rate (processed end to end) | 100.0% |" in report
    assert "## Reliability" in report and "## Processing time" in report
    assert "| Total |" in report


def test_timing_of_no_documents_is_empty() -> None:
    summary = summarise([])
    assert summary["reliability"]["success_rate"] is None
    assert summary["timing_seconds"]["total"] == {
        "mean": None,
        "median": None,
        "p95": None,
        "max": None,
    }
