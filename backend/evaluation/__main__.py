"""
Command line: generate a dataset, or run an evaluation.

    uv run python -m evaluation generate --out evaluation/dataset --count 40
    uv run python -m evaluation run --llm oracle      # harness check, free
    uv run python -m evaluation run --llm claude --yes  # real, costs money
"""

import argparse
import asyncio
import datetime as dt
import sys
from pathlib import Path

from app.core.config import get_settings
from app.extraction.llm import ClaudeInvoiceLLM, InvoiceLLM
from app.processing.ocr import OcrConfig
from evaluation.generate import generate
from evaluation.report import write_report
from evaluation.runner import NoisyLLM, OracleLLM, load_dataset, run

DEFAULT_DATASET = Path(__file__).parent / "dataset"
DEFAULT_RESULTS = Path(__file__).parent / "results"


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
    gen.add_argument("--out", type=Path, default=DEFAULT_DATASET)
    gen.add_argument("--count", type=int, default=40)
    gen.add_argument("--seed", type=int, default=2026)

    ev = commands.add_parser("run", help="measure extraction accuracy")
    ev.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    ev.add_argument("--out", type=Path, default=None, help="report directory")
    ev.add_argument("--llm", choices=["claude", "oracle", "noisy"], default="claude")
    ev.add_argument(
        "--noise", type=float, default=0.2, help="error rate for --llm noisy"
    )
    ev.add_argument("--limit", type=int, default=None, help="only the first N cases")
    ev.add_argument(
        "--yes", action="store_true", help="confirm spending money (claude)"
    )

    args = parser.parse_args(argv)
    if args.command == "generate":
        paths = generate(args.out, args.count, args.seed)
        print(f"Wrote {len(paths)} labelled cases to {args.out}")
        return 0

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
    out = args.out or DEFAULT_RESULTS / f"{dt.datetime.now():%Y%m%d-%H%M%S}-{args.llm}"
    settings = get_settings()
    config = OcrConfig(
        languages=settings.ocr_languages, timeout_seconds=settings.ocr_timeout_seconds
    )
    print(f"Evaluating {len(cases)} cases with {args.llm}…")
    results = asyncio.run(run(cases, _llm(args.llm, args.noise), config))
    summary = write_report(results, out, args.llm, str(args.dataset))
    errors = summary["errors"]
    if errors:
        print(
            f"\n{len(errors)} of {len(cases)} cases failed, e.g. {errors[0]['error']}"
        )
    if len(errors) == len(cases):
        print(f"No case could be scored. Details: {out / 'report.md'}")
        return 3
    print(
        f"\nDocuments fully correct: {summary['documents_exact']}% · "
        f"field accuracy: {summary['fields']['accuracy']}% · "
        f"hallucination rate: {summary['fields']['hallucination_rate']}%\n"
        f"Report: {out / 'report.md'}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
