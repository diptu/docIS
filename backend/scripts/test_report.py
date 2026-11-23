"""
Summarise a full test run as Markdown, from the reports `task test-report` saves:

    test-results/backend-junit.xml     pytest --junitxml
    test-results/coverage.xml          pytest-cov (Cobertura format)
    ../frontend/test-results/frontend-junit.xml   vitest --reporter=junit

Writes test-results/summary.md and prints it. Exits 1 if any test failed or
errored, so the task fails like the test run would.
"""

import datetime as dt
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
RESULTS = BACKEND / "test-results"
FRONTEND_JUNIT = BACKEND.parent / "frontend" / "test-results" / "frontend-junit.xml"

# Backend test folders, in the order they are reported.
LAYERS = {
    "unit": "Unit (pure logic)",
    "api": "API (HTTP layer)",
    "integration": "Integration (OCR, files, worker, PostgreSQL, embeddings)",
    "e2e": "End to end (whole app, in-process worker)",
}


def _cases(path: Path) -> list[ET.Element]:
    return list(ET.parse(path).getroot().iter("testcase"))


def _outcome(case: ET.Element) -> str:
    for tag in ("failure", "error", "skipped"):
        if case.find(tag) is not None:
            return tag
    return "passed"


def _totals(cases: list[ET.Element]) -> Counter[str]:
    counts = Counter(_outcome(case) for case in cases)
    counts["total"] = len(cases)
    return counts


def _seconds(path: Path) -> float:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    if root.get("time"):
        return float(root.get("time", 0))
    return sum(float(s.get("time", 0)) for s in suites)


def _row(name: str, counts: Counter[str], coverage: str, seconds: str) -> str:
    return (
        f"| {name} | {counts['total']} | {counts['passed']} | "
        f"{counts['failure'] + counts['error']} | {counts['skipped']} | "
        f"{coverage} | {seconds} |"
    )


def build() -> tuple[str, bool]:
    backend_junit = RESULTS / "backend-junit.xml"
    backend = _cases(backend_junit)
    rate = ET.parse(RESULTS / "coverage.xml").getroot().get("line-rate", "0")
    coverage = f"{float(rate) * 100:.2f}%"

    lines = [
        f"**Results** (full run, {dt.date.today():%d %B %Y}):",
        "",
        "| Suite | Collected | Passed | Failed | Skipped | Coverage | Time |",
        "|---|---|---|---|---|---|---|",
        _row(
            "Backend (pytest)",
            _totals(backend),
            coverage,
            f"{_seconds(backend_junit):.2f} s",
        ),
    ]
    failed = _totals(backend)["failure"] + _totals(backend)["error"]

    if FRONTEND_JUNIT.exists():
        frontend = _cases(FRONTEND_JUNIT)
        files = len({case.get("classname") for case in frontend})
        lines.append(
            _row(
                f"Frontend (Vitest, {files} files)",
                _totals(frontend),
                "n/a",
                f"{_seconds(FRONTEND_JUNIT):.2f} s",
            )
        )
        failed += _totals(frontend)["failure"] + _totals(frontend)["error"]

    lines += [
        "",
        "Backend tests by layer:",
        "",
        "| Layer | Tests | Passed |",
        "|---|---|---|",
    ]
    by_layer: dict[str, list[ET.Element]] = {}
    for case in backend:
        parts = (case.get("classname") or "").split(".")
        layer = parts[1] if len(parts) > 1 and parts[0] == "tests" else "other"
        by_layer.setdefault(layer, []).append(case)
    for layer in [*LAYERS, *sorted(set(by_layer) - set(LAYERS))]:
        if layer in by_layer:
            counts = _totals(by_layer[layer])
            label = LAYERS.get(layer, layer)
            lines.append(f"| {label} | {counts['total']} | {counts['passed']} |")

    failures = [
        f"- `{case.get('classname')}::{case.get('name')}`"
        for case in backend
        if _outcome(case) in ("failure", "error")
    ]
    if failures:
        lines += ["", "Failed:", *failures]
    return "\n".join(lines) + "\n", failed == 0


def main() -> int:
    summary, ok = build()
    (RESULTS / "summary.md").write_text(summary)
    print(summary)
    print(f"Written to {RESULTS / 'summary.md'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
