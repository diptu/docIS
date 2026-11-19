"""Generator, runner, report and CLI, end to end on a small dataset."""

import json
import random
from pathlib import Path

import pytest

from app.extraction.validation import CheckStatus, validate_amounts
from app.processing.ocr import OcrConfig
from evaluation.__main__ import main
from evaluation.generate import FORMATS, generate, make_invoice
from evaluation.report import summarise, write_report
from evaluation.runner import NoisyLLM, OracleLLM, load_dataset, run
from tests.helpers import requires_tesseract


def test_generated_invoices_add_up() -> None:
    rng = random.Random(1)
    for number in range(50):
        data, _ = make_invoice(rng, number)
        checks = validate_amounts(data)
        assert not [c for c in checks if c.status == CheckStatus.FAILED], data


def test_generation_is_deterministic(tmp_path: Path) -> None:
    generate(tmp_path / "a", 4, seed=5)
    generate(tmp_path / "b", 4, seed=5)

    for label in (tmp_path / "a").glob("*.json"):
        assert label.read_text() == (tmp_path / "b" / label.name).read_text()


@pytest.fixture(scope="module")
def dataset(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("dataset")
    generate(directory, len(FORMATS), seed=11)  # one case per format
    return directory


def test_dataset_loads_with_labels(dataset: Path) -> None:
    cases = load_dataset(dataset)

    assert sorted(case.format for case in cases) == sorted(FORMATS)
    assert all(case.expected.is_invoice for case in cases)


def test_dataset_rejects_labels_without_one_document(tmp_path: Path) -> None:
    (tmp_path / "orphan.json").write_text("{}")

    with pytest.raises(ValueError, match="exactly one document"):
        load_dataset(tmp_path)


@requires_tesseract
@pytest.mark.asyncio
async def test_oracle_scores_perfectly_on_every_format(
    dataset: Path, tmp_path: Path
) -> None:
    results = await run(
        load_dataset(dataset), OracleLLM(), OcrConfig(timeout_seconds=30)
    )

    summary = write_report(results, tmp_path / "report", "oracle", str(dataset))

    assert summary["errors"] == []
    assert summary["documents_exact"] == 100.0
    assert summary["fields"]["hallucination_rate"] == 0.0
    assert summary["line_items"]["f1"] == 100.0
    assert set(summary["by_format"]) == set(FORMATS)
    report = (tmp_path / "report" / "report.md").read_text()
    assert "| Documents fully correct (fields + line items) | 100.0% |" in report
    cases = json.loads((tmp_path / "report" / "cases.json").read_text())
    assert all(case["mistakes"] == [] for case in cases)


@requires_tesseract
@pytest.mark.asyncio
async def test_noisy_model_lowers_the_scores(dataset: Path) -> None:
    results = await run(
        load_dataset(dataset), NoisyLLM(rate=1.0), OcrConfig(timeout_seconds=30)
    )

    summary = summarise(results)

    assert summary["documents_exact"] == 0.0
    assert summary["fields"]["miss_rate"] > 0
    assert summary["fields"]["hallucination_rate"] > 0
    assert summary["line_items"]["recall"] < 100


@pytest.mark.asyncio
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


def test_cli(dataset: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["generate", "--out", str(tmp_path / "gen"), "--count", "2"]) == 0
    assert len(list((tmp_path / "gen").glob("*.json"))) == 2

    # Claude costs money: refuses without --yes.
    assert main(["run", "--dataset", str(dataset), "--llm", "claude"]) == 2
    assert "--yes" in capsys.readouterr().out

    assert main(["run", "--dataset", str(tmp_path / "empty"), "--llm", "oracle"]) == 1

    out = tmp_path / "out"
    code = main(
        [
            "run",
            "--dataset",
            str(tmp_path / "gen"),
            "--llm",
            "oracle",
            "--out",
            str(out),
        ]
    )
    assert code == 0
    assert (out / "report.md").exists() and (out / "summary.json").exists()


def test_cli_fails_loudly_when_no_case_can_be_scored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import evaluation.__main__ as cli

    class NoKey(OracleLLM):
        async def extract(self, document_text: str) -> object:  # type: ignore[override]
            raise RuntimeError("No Claude credentials are configured.")

    monkeypatch.setattr(cli, "_llm", lambda name, noise: NoKey())
    generate(tmp_path / "data", 2, seed=3)

    code = main(
        ["run", "--dataset", str(tmp_path / "data"), "--llm", "claude", "--yes"]
    )

    assert code == 3
    output = capsys.readouterr().out
    assert "2 of 2 cases failed" in output and "No Claude credentials" in output
