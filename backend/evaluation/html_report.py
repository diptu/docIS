"""
A self-contained HTML report of the latest evaluation runs.

    results/evaluation_report.html

Built from the results JSON files (``reporting.py``), rebuilt after every run,
or on demand with ``python -m evaluation report``. One file, inline CSS and a
few lines of script, no network access: it can be opened locally, attached or
archived. Every value from a dataset is HTML-escaped (labels may come from a
third party).
"""

import datetime as dt
import json
from collections.abc import Iterable, Sequence
from html import escape
from pathlib import Path
from typing import Any

from evaluation.reporting import (
    EVALUATION_REPORT,
    EXTRACTION_RESULTS,
    VALIDATION_RESULTS,
)

Row = Sequence[object]


# --- Formatting -----------------------------------------------------------


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}%"


def _num(value: float | int | None) -> str:
    if value is None:
        return "—"
    return f"{value:,}" if isinstance(value, int) else f"{value:,.2f}"


def _secs(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value * 1000:.0f} ms" if value < 1 else f"{value:.2f} s"


def _when(value: str | None) -> str:
    if not value:
        return "—"
    try:
        return dt.datetime.fromisoformat(value).strftime("%d %b %Y, %H:%M")
    except ValueError:
        return escape(value)


def _text(value: object) -> str:
    """A ground-truth or extracted value, shown as data."""
    if value is None:
        return '<span class="null">null</span>'
    if isinstance(value, float):
        return escape(f"{value:,.2f}")
    return escape(str(value))


def _read(path: Path) -> dict[str, Any] | None:
    try:
        content = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return content if isinstance(content, dict) else None


# --- Components -------------------------------------------------------------


def _tile(label: str, value: str, note: str = "") -> str:
    note_html = f'<div class="tile-note">{escape(note)}</div>' if note else ""
    return (
        f'<div class="tile"><div class="tile-label">{escape(label)}</div>'
        f'<div class="tile-value">{value}</div>{note_html}</div>'
    )


def _tiles(tiles: Iterable[str]) -> str:
    return f'<div class="tiles">{"".join(tiles)}</div>'


def _meter(value: float | None, label: str) -> str:
    """A 0-100% bar on a lighter track of the same hue (value printed beside)."""
    width = 0.0 if value is None else max(0.0, min(100.0, value))
    title = f"{label}: {_pct(value)}"
    return (
        f'<span class="meter" role="img" aria-label="{escape(title)}" '
        f'title="{escape(title)}"><span class="meter-fill" '
        f'style="width:{width:.1f}%"></span></span>'
    )


def _table(
    head: Sequence[str],
    rows: Iterable[Row],
    numeric: Sequence[int] = (),
    caption: str = "",
) -> str:
    cap = f"<caption>{escape(caption)}</caption>" if caption else ""
    th = "".join(
        f'<th scope="col"{" class=num" if i in numeric else ""}>{escape(h)}</th>'
        for i, h in enumerate(head)
    )
    body = "".join(
        "<tr>"
        + "".join(
            f"<td{' class=num' if i in numeric else ''}>{cell}</td>"
            for i, cell in enumerate(row)
        )
        + "</tr>"
        for row in rows
    )
    return (
        f'<div class="table-wrap"><table>{cap}<thead><tr>{th}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>"
    )


def _meter_cell(value: float | None, label: str) -> str:
    return f'<span class="meter-cell">{_meter(value, label)}<b>{_pct(value)}</b></span>'


def _section(title: str, body: str, intro: str = "") -> str:
    intro_html = f'<p class="intro">{intro}</p>' if intro else ""
    return f"<section><h2>{escape(title)}</h2>{intro_html}{body}</section>"


def _empty(message: str) -> str:
    return f'<p class="empty">{escape(message)}</p>'


# --- Extraction -------------------------------------------------------------


def _extraction(run: dict[str, Any]) -> str:
    s = run["summary"]
    fields, lines, rel = s["fields"], s["line_items"], s["reliability"]
    timing = s["timing_seconds"]
    flag = s.get("flagging")

    tiles = [
        _tile("Documents", _num(s["cases"]), f"{len(s['errors'])} failed"),
        _tile("Processed successfully", _pct(rel["success_rate"])),
        _tile("Documents fully correct", _pct(s["documents_exact"])),
        _tile("Field accuracy", _pct(fields["accuracy"]), "incl. correct nulls"),
        _tile(
            "Hallucination rate",
            _pct(fields["hallucination_rate"]),
            "values invented for absent fields",
        ),
        _tile(
            "Line items F1",
            _pct(lines["f1"]),
            f"precision {_pct(lines['precision'])} · recall {_pct(lines['recall'])}",
        ),
        _tile("Median time per document", _secs(timing["total"]["median"])),
        _tile("Estimated cost", f"${s['estimated_cost_usd']:,.2f}"),
    ]
    if flag:
        tiles.append(
            _tile(
                "Inconsistent documents flagged",
                _pct(flag["recall"]),
                f"{flag['true_positives']} of {flag['should_flag']} · "
                f"precision {_pct(flag['precision'])}",
            )
        )

    per_field = _table(
        ["Field", "Accuracy", "Wrong", "Missed", "Hallucinated"],
        [
            (
                f"<code>{escape(name)}</code>",
                _meter_cell(m["accuracy"], name),
                m["counts"]["wrong"],
                m["counts"]["missed"],
                m["counts"]["hallucinated"],
            )
            for name, m in sorted(
                s["per_field"].items(), key=lambda kv: (kv[1]["accuracy"] or 0, kv[0])
            )
        ],
        numeric=(2, 3, 4),
        caption="Lowest accuracy first",
    )
    by_format = _table(
        ["Format / category", "Cases", "Fully correct", "Field accuracy"],
        [
            (
                escape(name),
                g["cases"],
                _meter_cell(g["documents_exact"], f"{name} fully correct"),
                _meter_cell(g["field_accuracy"], f"{name} field accuracy"),
            )
            for name, g in sorted(s["by_format"].items())
        ],
        numeric=(1,),
    )
    stages = rel["failures_by_stage"]
    reliability = _table(
        ["Stage", "Mean", "Median", "p95", "Max"],
        [
            (label, *(_secs(timing[key][k]) for k in ("mean", "median", "p95", "max")))
            for label, key in (
                ("Content extraction (parse / OCR)", "content_extraction"),
                ("AI extraction (model call)", "ai_extraction"),
                ("Total", "total"),
            )
        ],
        numeric=(1, 2, 3, 4),
        caption=(
            f"Per successful document. Failures: content extraction "
            f"{stages['content_extraction']}, AI extraction "
            f"{stages['ai_extraction']}, other {stages['other']}."
        ),
    )
    lf = lines["field_accuracy"]
    line_table = _table(
        ["Expected", "Extracted", "Matched", "Line field accuracy"],
        [
            (
                _num(lines["expected"]),
                _num(lines["extracted"]),
                _num(lines["matched"]),
                _meter_cell(lf["accuracy"], "line field accuracy"),
            )
        ],
        numeric=(0, 1, 2),
    )
    notice = {
        "oracle": "Harness check: the “model” returned the ground truth, so these "
        "scores test the evaluation itself (file reading, OCR, scoring), not "
        "extraction quality. Run with --llm claude for real results.",
        "noisy": "Harness check: the ground truth with deliberate errors, to show the "
        "metrics detect them. Not a real extraction run.",
    }.get(str(run["model"]))
    meta = (
        (f'<span class="notice">{escape(notice)}</span>' if notice else "")
        + f"Model <code>{escape(str(run['model']))}</code> · dataset "
        f"<code>{escape(str(run['dataset']))}</code> · run {_when(run['run_at'])}"
    )
    return (
        _section("Extraction", _tiles(tiles), meta)
        + _section("Accuracy per field", per_field)
        + _section("By format / category", by_format)
        + _section("Line items", line_table)
        + _section("Processing time", reliability)
        + _cases(run.get("cases", []))
    )


def _cases(cases: list[dict[str, Any]]) -> str:
    wrong = [c for c in cases if c.get("error") or c.get("mistakes")]
    if not cases:
        return ""
    if not wrong:
        return _section("Cases", _empty("Every case was extracted correctly."))
    items = []
    for c in wrong:
        if c.get("error"):
            body = f'<p class="error">{escape(c["error"])}</p>'
            badge = '<span class="badge bad">failed</span>'
        else:
            lines = c.get("lines") or {}
            body = _table(
                ["Field", "Expected", "Extracted", "Outcome"],
                [
                    (
                        f"<code>{escape(m['field'])}</code>",
                        _text(m["expected"]),
                        _text(m["actual"]),
                        f'<span class="outcome {escape(m["outcome"])}">'
                        f"{escape(m['outcome'].replace('_', ' '))}</span>",
                    )
                    for m in c["mistakes"]
                ],
                caption=(
                    f"Line items: {lines.get('matched', 0)} of "
                    f"{lines.get('expected', 0)} matched, "
                    f"{lines.get('extracted', 0)} extracted"
                ),
            )
            n = len(c["mistakes"])
            badge = f'<span class="badge">{n} mistake{"s" if n != 1 else ""}</span>'
        search = escape(f"{c['case']} {c.get('format', '')}".lower())
        items.append(
            f'<details class="case" data-search="{search}"><summary>'
            f"<code>{escape(c['case'])}</code>"
            f'<span class="muted">{escape(str(c.get("format", "")))}</span>{badge}'
            f"</summary>{body}</details>"
        )
    controls = (
        '<label class="filter">Filter cases '
        '<input type="search" id="case-filter" placeholder="name or category"></label>'
    )
    intro = (
        f"{len(wrong)} of {len(cases)} cases have a mistake or failed. "
        "Expand one to see expected and extracted values."
    )
    return _section(
        "Cases with mistakes",
        controls + f'<div id="cases">{"".join(items)}</div>',
        intro,
    )


# --- Validation -------------------------------------------------------------


def _validation(run: dict[str, Any]) -> str:
    s = run["summary"]
    c = s["classification"]
    tiles = [
        _tile(
            "Invoices", _num(s["invoices"]), f"{s['injected_errors']} injected errors"
        ),
        _tile("Detection rate", _pct(s["detection_rate"]), "injected errors caught"),
        _tile(
            "False-alarm rate",
            _pct(s["false_alarm_rate"]),
            "correct invoices failing a check",
        ),
        _tile("Precision", _pct(c["precision"])),
        _tile("Recall", _pct(c["recall"])),
        _tile("Scenarios fully correct", _pct(s["scenarios_correct"])),
    ]
    per_check = _table(
        ["Check", "Must fail", "Caught", "Recall", "False alarms"],
        [
            (
                f"<code>{escape(name)}</code>",
                m["expected_failures"],
                m["caught"],
                _meter_cell(m["recall"], f"{name} recall"),
                m["false_alarms"],
            )
            for name, m in s["per_check"].items()
        ],
        numeric=(1, 2, 4),
    )
    by_error = _table(
        ["Injected error", "Applied", "Detected", "Detection rate"],
        [
            (
                f"<code>{escape(name)}</code>",
                g["applied"],
                g["detected"],
                _meter_cell(g["detection_rate"], f"{name} detection"),
            )
            for name, g in s["by_injected_error"].items()
        ],
        numeric=(1, 2),
    )
    wrong = run.get("incorrect_scenarios", [])
    if wrong:
        scenarios = _table(
            ["Case", "Scenario", "Should fail", "Failed", "Problem"],
            [
                (
                    f"<code>{escape(x['case'])}</code>",
                    escape(x["injected_error"] or "as labelled"),
                    escape(", ".join(x["expected_to_fail"]) or "—"),
                    escape(", ".join(x["failed"]) or "—"),
                    (
                        f'<span class="outcome missed">missed '
                        f"{escape(', '.join(x['missed']))}</span>"
                        if x["missed"]
                        else f'<span class="outcome hallucinated">false alarm: '
                        f"{escape(', '.join(x['false_alarms']))}</span>"
                    ),
                )
                for x in wrong
            ],
        )
    else:
        scenarios = _empty("Every scenario behaved as expected.")
    skipped = s.get("invoices_without_expectations") or []
    meta = (
        f"Dataset <code>{escape(str(run['dataset']))}</code>"
        f" · run {_when(run['run_at'])}"
        + (f" · {len(skipped)} skipped (no expectations)" if skipped else "")
    )
    return (
        _section("Validation (amount checks)", _tiles(tiles), meta)
        + _section("Per check", per_check)
        + _section("By injected error", by_error)
        + _section(f"Scenarios that went wrong ({len(wrong)})", scenarios)
    )


# --- Page -------------------------------------------------------------------

STYLE = """
:root {
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e;
  --muted: #6b6a65; --hairline: #e1e0d9; --border: rgba(11,11,11,0.10);
  --accent: #2a78d6; --track: #cde2fb; --bad: #b42323; --bad-bg: #fbeaea;
  --warn-bg: #fdf3dc; --warn: #7a5200; --good: #006300; --code-bg: #f0efec;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink-2: #c3c2b7;
    --muted: #9a9890; --hairline: #2c2c2a; --border: rgba(255,255,255,0.10);
    --accent: #5598e7; --track: #184f95; --bad: #f08a8a; --bad-bg: #3a1a1a;
    --warn-bg: #3a2e12; --warn: #f5c76a; --good: #0ca30c; --code-bg: #262624;
  }
}
:root[data-theme="dark"] {
  --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink-2: #c3c2b7;
  --muted: #9a9890; --hairline: #2c2c2a; --border: rgba(255,255,255,0.10);
  --accent: #5598e7; --track: #184f95; --bad: #f08a8a; --bad-bg: #3a1a1a;
  --warn-bg: #3a2e12; --warn: #f5c76a; --good: #0ca30c; --code-bg: #262624;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--page); color: var(--ink);
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
main { max-width: 1080px; margin: 0 auto; padding: 32px 16px 64px; min-width: 0; }
html, body { overflow-x: hidden; }
header h1 { margin: 0 0 4px; font-size: 26px; letter-spacing: -0.01em; }
header p { margin: 0; color: var(--ink-2); }
nav { display: flex; flex-wrap: wrap; gap: 8px 16px; margin: 16px 0 8px; }
nav a { color: var(--accent); text-decoration: none; font-weight: 500; }
nav a:hover { text-decoration: underline; }
h2 { font-size: 18px; margin: 40px 0 6px; }
section > h2 + .intro, .intro { margin: 0 0 14px; color: var(--ink-2); }
.part { border-top: 1px solid var(--hairline); margin-top: 40px; }
.part > h2:first-child { margin-top: 28px; }
code {
  font: 13px ui-monospace, SFMono-Regular, Menlo, monospace;
  background: var(--code-bg); padding: 1px 5px; border-radius: 4px;
  overflow-wrap: anywhere;
}
td code { overflow-wrap: normal; }
.tiles {
  display: grid; gap: 12px;
  grid-template-columns: repeat(auto-fill, minmax(min(170px, 100%), 1fr));
}
.tile {
  background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
  padding: 14px 16px;
}
.tile-label { color: var(--ink-2); font-size: 13px; }
.tile-value {
  font-size: 26px; font-weight: 600; margin-top: 2px;
  font-variant-numeric: tabular-nums;
}
.tile-note { color: var(--muted); font-size: 12px; margin-top: 2px; }
.table-wrap {
  overflow-x: auto; background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px;
}
table { border-collapse: collapse; width: 100%; font-size: 14px; }
caption {
  caption-side: bottom; text-align: left; padding: 8px 12px; color: var(--muted);
  font-size: 12px;
}
th, td {
  text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--hairline);
  vertical-align: middle;
}
th { color: var(--ink-2); font-weight: 500; font-size: 13px; white-space: nowrap; }
tbody tr:last-child td { border-bottom: 0; }
tbody tr:hover td { background: color-mix(in srgb, var(--accent) 6%, transparent); }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.meter-cell { display: flex; align-items: center; gap: 10px; min-width: 170px; }
.meter-cell b { font-weight: 600; font-variant-numeric: tabular-nums; min-width: 52px; }
.meter {
  flex: 1; height: 8px; border-radius: 4px; background: var(--track);
  overflow: hidden; min-width: 80px;
}
.meter-fill {
  display: block; height: 100%; background: var(--accent); border-radius: 4px;
}
.null { color: var(--muted); font-style: italic; }
.empty { color: var(--ink-2); }
.error { color: var(--bad); margin: 8px 0 0; }
.muted { color: var(--muted); font-size: 13px; }
.badge {
  margin-left: auto; font-size: 12px; padding: 1px 8px; border-radius: 999px;
  background: var(--warn-bg); color: var(--warn); white-space: nowrap;
}
.badge.bad { background: var(--bad-bg); color: var(--bad); }
.outcome { font-size: 13px; white-space: nowrap; }
.outcome.wrong, .outcome.hallucinated { color: var(--bad); }
.outcome.missed { color: var(--warn); }
.filter {
  display: flex; align-items: center; gap: 8px; margin: 0 0 12px; color: var(--ink-2);
}
.filter input {
  flex: 1; max-width: 320px; font: inherit; padding: 6px 10px; border-radius: 8px;
  border: 1px solid var(--border); background: var(--surface); color: var(--ink);
}
.case {
  background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
  margin-bottom: 8px;
}
.case summary {
  display: flex; align-items: center; gap: 10px; padding: 10px 14px; cursor: pointer;
  flex-wrap: wrap;
}
.case[open] summary { border-bottom: 1px solid var(--hairline); }
.case .table-wrap { border: 0; border-radius: 0 0 10px 10px; }
.case .error { padding: 0 14px 12px; }
.notice {
  display: block; margin: 0 0 10px; padding: 10px 14px; border-radius: 8px;
  background: var(--warn-bg); color: var(--warn);
}
footer { margin-top: 48px; color: var(--muted); font-size: 12px; }
@media (max-width: 560px) {
  .tile-value { font-size: 22px; }
  th, td { padding: 8px; }
}
"""

SCRIPT = """
const input = document.getElementById("case-filter");
if (input) input.addEventListener("input", () => {
  const q = input.value.trim().toLowerCase();
  for (const el of document.querySelectorAll("#cases .case"))
    el.hidden = q !== "" && !el.dataset.search.includes(q);
});
"""


def render(results_dir: Path) -> str:
    """The report page for the runs in ``results_dir``."""
    extraction = _read(results_dir / EXTRACTION_RESULTS)
    validation = _read(results_dir / VALIDATION_RESULTS)
    parts, links = [], []
    if extraction:
        parts.append(
            f'<div class="part" id="extraction">{_extraction(extraction)}</div>'
        )
        links.append('<a href="#extraction">Extraction</a>')
        if any(
            c.get("error") or c.get("mistakes") for c in extraction.get("cases", [])
        ):
            links.append('<a href="#cases">Cases with mistakes</a>')
    if validation:
        parts.append(
            f'<div class="part" id="validation">{_validation(validation)}</div>'
        )
        links.append('<a href="#validation">Validation</a>')
    if not parts:
        parts.append(
            _empty(
                "No results yet. Run `python -m evaluation run` or "
                "`python -m evaluation validate` first."
            )
        )
    generated = dt.datetime.now().strftime("%d %b %Y, %H:%M")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Evaluation report</title>
<style>{STYLE}</style>
</head>
<body>
<main>
<header>
<h1>Evaluation report</h1>
<p>Invoice extraction and amount-check accuracy ·
<code>{escape(str(results_dir))}</code></p>
<nav>{"".join(links)}</nav>
</header>
{"".join(parts)}
<footer>Generated {generated} from the results JSON in this folder.</footer>
</main>
<script>{SCRIPT}</script>
</body>
</html>
"""


def write_report(results_dir: Path) -> Path:
    """Write ``evaluation_report.html`` next to the results it describes."""
    path = results_dir / EVALUATION_REPORT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(results_dir))
    return path
