"""
Command line: generate a dataset, or run an evaluation.

    uv run python -m evaluation generate --count 40
    uv run python -m evaluation run --llm oracle      # harness check, free
    uv run python -m evaluation run --llm claude --yes  # real, costs money
    uv run python -m evaluation validate              # amount checks, free
    uv run python -m evaluation ocr                   # OCR on poor images, free

Results go to evaluation/results/: extraction_results.json,
validation_results.json and evaluation_summary.json (both, in brief).
"""

import argparse
import asyncio
import sys
from pathlib import Path

from app.core.config import get_settings
from app.extraction.chunked import ChunkingConfig
from app.extraction.llm import ClaudeInvoiceLLM, InvoiceLLM
from app.processing.ocr import OcrConfig
from evaluation import evaluate_validation
from evaluation.evaluate_extraction import (
    NoisyLLM,
    OracleLLM,
    load_dataset,
    render_markdown,
    run,
    write_results,
)
from evaluation.generate import generate
from evaluation.reconciliation import (
    PAIRS_DIR,
    evaluate_pairs,
    generate_pairs,
    render_summary,
    write_reconciliation,
)
from evaluation.reporting import (
    DATASET_DIR,
    EVALUATION_REPORT,
    EVALUATION_SUMMARY,
    EXPECTATIONS_DIR,
    RESULTS_DIR,
)


def _llm(name: str, noise: float) -> InvoiceLLM:
    if name == "oracle":
        return OracleLLM()
    if name == "noisy":
        return NoisyLLM(noise)
    settings = get_settings()
    return ClaudeInvoiceLLM(
        model=settings.llm_model,
        effort=settings.llm_effort,
        max_tokens=settings.llm_max_tokens,
        timeout_seconds=settings.llm_timeout_seconds,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evaluation")
    commands = parser.add_subparsers(dest="command", required=True)

    gen = commands.add_parser("generate", help="write synthetic labelled invoices")
    gen.add_argument("--dataset", type=Path, default=DATASET_DIR)
    gen.add_argument("--expectations", type=Path, default=None)
    gen.add_argument("--count", type=int, default=40)
    gen.add_argument("--seed", type=int, default=2026)

    conv = commands.add_parser(
        "convert-third-party",
        help="convert evaluation/third_party to the harness layout",
    )
    conv.add_argument("--source", type=Path, default=None)
    conv.add_argument("--out", type=Path, default=None)

    rgen = commands.add_parser(
        "reconcile-generate", help="write labelled PDF-PDF and PDF-Excel pairs"
    )
    rgen.add_argument("--out", type=Path, default=PAIRS_DIR)
    rgen.add_argument("--seed", type=int, default=2026)
    rgen.add_argument("--repeats", type=int, default=3)

    rec = commands.add_parser(
        "reconcile", help="measure cross-document comparison on labelled pairs"
    )
    rec.add_argument("--pairs", type=Path, default=PAIRS_DIR)
    rec.add_argument("--out", type=Path, default=RESULTS_DIR, help="results folder")
    rec.add_argument(
        "--extraction",
        choices=["exact", "claude"],
        default="exact",
        help="invoices for the PDF-PDF invoice comparison: the labels (free) "
        "or extracted with Claude (costs money)",
    )
    rec.add_argument("--yes", action="store_true", help="confirm spending money")

    rep = commands.add_parser("report", help="rebuild the HTML report from results")
    rep.add_argument("--results", type=Path, default=RESULTS_DIR)

    ev = commands.add_parser("run", help="measure extraction accuracy")
    ev.add_argument("--dataset", type=Path, default=DATASET_DIR)
    ev.add_argument("--out", type=Path, default=RESULTS_DIR, help="results folder")
    ev.add_argument("--llm", choices=["claude", "oracle", "noisy"], default="claude")
    ev.add_argument(
        "--noise", type=float, default=0.2, help="error rate for --llm noisy"
    )
    ev.add_argument("--limit", type=int, default=None, help="only the first N cases")
    ev.add_argument(
        "--chunk-chars",
        type=int,
        default=None,
        help="extraction part size (default: LLM_CHUNK_CHARS); small values "
        "force the split-and-merge path on short documents",
    )
    ev.add_argument(
        "--yes", action="store_true", help="confirm spending money (claude)"
    )

    val = commands.add_parser("validate", help="measure the amount checks")
    val.add_argument("--dataset", type=Path, default=DATASET_DIR)
    val.add_argument("--expectations", type=Path, default=None)
    val.add_argument("--out", type=Path, default=RESULTS_DIR, help="results folder")

    ocr = commands.add_parser(
        "ocr", help="measure OCR on skewed, blurred, low-res, dark and noisy images"
    )
    ocr.add_argument("--layout", choices=["sparse", "dense", "both"], default="both")
    ocr.add_argument(
        "--dataset",
        type=Path,
        default=None,
        help="labelled dataset whose photos and scans to OCR as well "
        "(e.g. evaluation/third_party_eval/dataset)",
    )
    ocr.add_argument("--out", type=Path, default=RESULTS_DIR, help="results folder")

    args = parser.parse_args(argv)
    if args.command == "ocr":
        from evaluation import ocr_benchmark

        layouts = ["sparse", "dense"] if args.layout == "both" else [args.layout]
        measured = ocr_benchmark.run(layouts, args.dataset)
        path = ocr_benchmark.write_results(measured, args.out)
        print(ocr_benchmark.render_markdown(measured))
        from evaluation.html_report import write_report

        print(f"\nResults: {path}\nReport: {write_report(args.out)}")
        return 0

    if args.command == "generate":
        paths = generate(args.dataset, args.count, args.seed, args.expectations)
        print(f"Wrote {len(paths)} labelled cases to {args.dataset}")
        return 0

    if args.command == "reconcile-generate":
        count = generate_pairs(args.out, args.seed, args.repeats)
        print(f"Wrote {count} labelled pairs to {args.out}")
        return 0

    if args.command == "reconcile":
        if args.extraction == "claude" and not args.yes:
            print("Claude extraction costs money; re-run with --yes to proceed.")
            return 2
        settings = get_settings()
        config = OcrConfig(
            languages=settings.ocr_languages,
            timeout_seconds=settings.ocr_timeout_seconds,
        )
        llm = _llm("claude", 0.0) if args.extraction == "claude" else None
        measured = evaluate_pairs(args.pairs, config, args.extraction, llm)
        path = write_reconciliation(measured, args.out)
        print("\n" + render_summary(measured))
        print(f"Results: {path.parent}")
        return 0

    if args.command == "report":
        from evaluation.html_report import write_report

        print(f"Report: {write_report(args.results)}")
        return 0

    if args.command == "convert-third-party":
        from evaluation.convert_third_party import OUT_DIR, SOURCE_DIR, convert

        out = args.out or OUT_DIR
        count = convert(args.source or SOURCE_DIR, out)
        print(f"Converted {count} cases to {out}")
        return 0

    if args.command == "validate":
        expectations = args.expectations or (
            EXPECTATIONS_DIR
            if args.dataset == DATASET_DIR
            else args.dataset.parent / "validation_expectations"
        )
        return _validate(args.dataset, expectations, args.out)

    cases = load_dataset(args.dataset)[: args.limit]
    if not cases:
        print(f"No cases in {args.dataset}; run `python -m evaluation generate` first.")
        return 1
    if args.llm == "claude" and not args.yes:
        settings = get_settings()
        print(
            f"This sends {len(cases)} documents to {settings.llm_model} and costs "
            "money (roughly a few cents per invoice). Re-run with --yes to proceed.",
        )
        return 2
    out = args.out
    settings = get_settings()
    config = OcrConfig(
        languages=settings.ocr_languages, timeout_seconds=settings.ocr_timeout_seconds
    )
    print(f"Evaluating {len(cases)} cases with {args.llm}…")
    chunking = ChunkingConfig(
        chunk_chars=args.chunk_chars or settings.llm_chunk_chars,
        max_parts=settings.llm_max_parts,
        concurrency=settings.llm_part_concurrency,
    )
    results = asyncio.run(run(cases, _llm(args.llm, args.noise), config, chunking))
    summary = write_results(results, out, args.llm, str(args.dataset))
    print("\n" + render_markdown(summary, args.llm, str(args.dataset)))
    errors = summary["errors"]
    if errors:
        print(
            f"\n{len(errors)} of {len(cases)} cases failed, e.g. {errors[0]['error']}"
        )
    if len(errors) == len(cases):
        print(f"No case could be scored. Details: {out}")
        return 3
    reliability = summary["reliability"]
    print(
        f"\nSuccess rate: {reliability['success_rate']}% · "
        f"failure rate: {reliability['failure_rate']}% · "
        f"median time: {summary['timing_seconds']['total']['median']} s\n"
        f"Documents fully correct: {summary['documents_exact']}% · "
        f"field accuracy: {summary['fields']['accuracy']}% · "
        f"hallucination rate: {summary['fields']['hallucination_rate']}%\n"
        f"Results: {out} (summary: {EVALUATION_SUMMARY}, report: {EVALUATION_REPORT})"
    )
    return 0


def _validate(dataset: Path, expectations: Path, out: Path) -> int:
    cases = load_dataset(dataset)
    if not cases:
        print(f"No cases in {dataset}; run `python -m evaluation generate` first.")
        return 1
    result = evaluate_validation.run(
        cases, evaluate_validation.load_expectations(expectations)
    )
    summary = evaluate_validation.write_results(
        result, out, str(dataset), str(expectations)
    )
    if not summary["invoices"]:
        print(f"No validation expectations in {expectations} match the dataset.")
        return 1
    print(
        f"Validation over {summary['invoices']} invoices and "
        f"{summary['injected_errors']} injected errors:\n"
        f"Detection rate: {summary['detection_rate']}% · "
        f"false-alarm rate: {summary['false_alarm_rate']}% · "
        f"scenarios fully correct: {summary['scenarios_correct']}%"
    )
    for name, group in summary["by_injected_error"].items():
        print(f"  {name}: {group['detected']}/{group['applied']} detected")
    if missing := summary["invoices_without_expectations"]:
        print(f"Skipped (no expectations): {', '.join(missing)}")
    print(f"Results: {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
