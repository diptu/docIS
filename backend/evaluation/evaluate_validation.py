"""
Validation evaluation: do the amount checks (``app.extraction.validation``)
fail exactly when they should?

Each ground-truth invoice has ``validation_expectations/<invoice number>.json``:

    {
      "invoice_number": "NW-2026-0001",
      "expected_failed_checks": [],          # on the invoice as labelled
      "should_flag": false,                  # optional: some check must fail
      "injected_errors": [
        {
          "name": "subtotal",
          "description": "Subtotal increased by 10.00",
          "set": {"subtotal": 22596.37},     # dotted paths: "line_items.0.amount"
          "must_fail": ["total"]
        }
      ]
    }

The invoice as labelled must fail exactly ``expected_failed_checks`` (any
other failure is a false alarm). Each injected error is applied to the ground
truth on its own, and every check in ``must_fail`` has to fail. Other checks
may fail too, as a knock-on effect. The generator writes these files. For a
real invoice, write one by hand (an empty ``injected_errors`` is fine).

Free and deterministic: no model is called.
"""

import datetime as dt
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.extraction.schema import InvoiceData
from app.extraction.validation import CheckCode, CheckStatus, validate_amounts
from evaluation.evaluate_extraction import Case
from evaluation.metrics import binary_classification, percent
from evaluation.reporting import VALIDATION_RESULTS, write_json, write_summary

CENT = 0.01


# --- Expectations ---------------------------------------------------------


@dataclass(frozen=True)
class InjectedError:
    name: str
    description: str
    set: dict[str, Any]
    must_fail: tuple[str, ...]


@dataclass(frozen=True)
class Expectation:
    invoice_number: str
    expected_failed_checks: tuple[str, ...]
    injected_errors: tuple[InjectedError, ...]
    # The labelled invoice doesn't add up, and some check (whichever) must say
    # so: for datasets that label "should be flagged" without naming a check.
    should_flag: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "invoice_number": self.invoice_number,
            **({"should_flag": True} if self.should_flag else {}),
            "expected_failed_checks": list(self.expected_failed_checks),
            "injected_errors": [
                {
                    "name": e.name,
                    "description": e.description,
                    "set": e.set,
                    "must_fail": list(e.must_fail),
                }
                for e in self.injected_errors
            ],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Expectation":
        errors = tuple(
            InjectedError(
                name=e["name"],
                description=e.get("description", ""),
                set=e["set"],
                must_fail=tuple(e["must_fail"]),
            )
            for e in data.get("injected_errors", [])
        )
        expected = tuple(data.get("expected_failed_checks", []))
        named = set(expected).union(*(e.must_fail for e in errors))
        if unknown := named - {code.value for code in CheckCode}:
            raise ValueError(f"Unknown check(s): {', '.join(sorted(unknown))}")
        return cls(
            data["invoice_number"],
            expected,
            errors,
            should_flag=bool(data.get("should_flag", False)),
        )


def _money(value: float) -> float:
    return round(value, 2)


def expectation_for(data: InvoiceData) -> Expectation:
    """Expectations for a consistent (generated) invoice: no check fails as
    labelled, plus one injected error per kind the invoice has the fields
    for. ``must_fail`` follows from what each check compares, not from
    running the checks."""
    errors: list[InjectedError] = []
    lines = data.line_items
    subtotal, tax, total = data.subtotal, data.tax_amount, data.total_amount

    priced = [
        i
        for i, item in enumerate(lines)
        if None not in (item.quantity, item.unit_price, item.amount)
    ]
    if priced:
        i = priced[0]
        price = _money(lines[i].unit_price * 1.1 + 1)  # type: ignore[operator]
        errors.append(
            InjectedError(
                "line_unit_price",
                f"Line {i + 1} unit price changed to {price:.2f}",
                {f"line_items.{i}.unit_price": price},
                ("line_amounts",),
            )
        )

    amounts = [i for i, item in enumerate(lines) if item.amount is not None]
    if amounts:
        i = amounts[0]
        must_fail = ["line_amounts"] if i in priced else []
        lines_sum = sum(lines[j].amount for j in amounts) + 9  # type: ignore[misc]
        # The subtotal check also accepts tax-inclusive lines that add up to
        # the total: only expect a failure when they don't.
        if (
            len(amounts) == len(lines)
            and subtotal is not None
            and (total is None or abs(lines_sum - total) > CENT)
        ):
            must_fail.append("lines_subtotal")
        if must_fail:
            errors.append(
                InjectedError(
                    "line_amount",
                    f"Line {i + 1} amount increased by 9.00",
                    {f"line_items.{i}.amount": _money(lines[i].amount + 9)},  # type: ignore[operator]
                    tuple(must_fail),
                )
            )

    if subtotal is not None and tax is not None and total is not None:
        taxed = bool(lines) and all(
            item.amount is not None and item.tax_rate is not None for item in lines
        )
        errors += [
            InjectedError(
                "subtotal",
                "Subtotal increased by 10.00",
                {"subtotal": _money(subtotal + 10)},
                ("total",),
            ),
            InjectedError(
                "tax_amount",
                "Tax amount increased by 5.00",
                {"tax_amount": _money(tax + 5)},
                ("tax_amount", "total") if taxed else ("total",),
            ),
            InjectedError(
                "total_amount",
                "Total increased by 10.00",
                {"total_amount": _money(total + 10)},
                ("total",),
            ),
        ]

    if total is not None:
        errors.append(
            InjectedError(
                "amount_due_over_total",
                "Amount due set 50.00 above the total",
                {"amount_due": _money(total + 50)},
                ("amount_due",),
            )
        )

    return Expectation(data.invoice_number or "", (), tuple(errors))


def load_expectations(directory: Path) -> dict[str, Expectation]:
    """Expectations by file name (the invoice number)."""
    return {
        path.stem: Expectation.from_json(json.loads(path.read_text()))
        for path in sorted(directory.glob("*.json"))
    }


# --- Running --------------------------------------------------------------


def apply(data: InvoiceData, changes: dict[str, Any]) -> InvoiceData:
    """``data`` with dotted-path fields replaced (``line_items.0.amount``)."""
    raw = data.model_dump(mode="json")
    for path, value in changes.items():
        *parents, leaf = path.split(".")
        target: Any = raw
        for key in parents:
            target = target[int(key)] if isinstance(target, list) else target[key]
        if isinstance(target, list):
            target[int(leaf)] = value
        elif leaf in target:
            target[leaf] = value
        else:
            raise KeyError(f"No field {path!r}")
    return InvoiceData.model_validate(raw)


def failed_checks(data: InvoiceData) -> tuple[str, ...]:
    return tuple(
        c.code.value for c in validate_amounts(data) if c.status == CheckStatus.FAILED
    )


@dataclass(frozen=True)
class Scenario:
    case: str
    error: str | None  # None: the invoice as labelled
    expected: tuple[str, ...]  # checks that must fail
    failed: tuple[str, ...]  # checks that did fail
    must_flag: bool = False  # some check, whichever, must fail

    @property
    def injected(self) -> bool:
        return self.error is not None

    @property
    def should_fail(self) -> bool:
        return bool(self.expected) or self.must_flag

    @property
    def missed(self) -> tuple[str, ...]:
        if self.must_flag and not self.failed:
            return ("any check",)
        return tuple(c for c in self.expected if c not in self.failed)

    @property
    def false_alarms(self) -> tuple[str, ...]:
        """Unexpected failures. Only counted as labelled: an injected error
        may also break checks it doesn't target, and an invoice that should
        be flagged may fail any check."""
        if self.injected or self.must_flag:
            return ()
        return tuple(c for c in self.failed if c not in self.expected)

    @property
    def correct(self) -> bool:
        return not self.missed and not self.false_alarms


@dataclass(frozen=True)
class ValidationRun:
    scenarios: list[Scenario]
    without_expectations: list[str]


def run(cases: Sequence[Case], expectations: dict[str, Expectation]) -> ValidationRun:
    scenarios: list[Scenario] = []
    skipped: list[str] = []
    for case in cases:
        expectation = expectations.get(case.name)
        if expectation is None:
            skipped.append(case.name)
            continue
        scenarios.append(
            Scenario(
                case.name,
                None,
                expectation.expected_failed_checks,
                failed_checks(case.expected),
                must_flag=expectation.should_flag,
            )
        )
        for error in expectation.injected_errors:
            mutated = apply(case.expected, error.set)
            scenarios.append(
                Scenario(case.name, error.name, error.must_fail, failed_checks(mutated))
            )
    return ValidationRun(scenarios, skipped)


# --- Metrics --------------------------------------------------------------


def summarise(result: ValidationRun) -> dict[str, Any]:
    scenarios = result.scenarios
    labelled = [s for s in scenarios if not s.injected]
    injected = [s for s in scenarios if s.injected]

    # As a detector: should this scenario be flagged, and was it?
    should = [s for s in scenarios if s.should_fail]
    should_not = [s for s in scenarios if not s.should_fail]
    tp = sum(1 for s in should if not s.missed)
    fp = sum(1 for s in should_not if s.false_alarms)

    per_check = {}
    for code in CheckCode:
        name = code.value
        must = [s for s in scenarios if name in s.expected]
        caught = sum(1 for s in must if name in s.failed)
        per_check[name] = {
            "expected_failures": len(must),
            "caught": caught,
            "recall": percent(caught, len(must)),
            "false_alarms": sum(1 for s in labelled if name in s.false_alarms),
        }

    by_error: dict[str, dict[str, Any]] = {}
    for s in injected:
        group = by_error.setdefault(str(s.error), {"applied": 0, "detected": 0})
        group["applied"] += 1
        group["detected"] += not s.missed
    for group in by_error.values():
        group["detection_rate"] = percent(group["detected"], group["applied"])

    return {
        "invoices": len(labelled),
        "invoices_without_expectations": result.without_expectations,
        "injected_errors": len(injected),
        "scenarios_correct": percent(sum(s.correct for s in scenarios), len(scenarios)),
        "detection_rate": percent(
            sum(1 for s in injected if not s.missed), len(injected)
        ),
        "false_alarm_rate": percent(
            sum(1 for s in labelled if s.false_alarms), len(labelled)
        ),
        "classification": binary_classification(
            tp=tp, fp=fp, fn=len(should) - tp, tn=len(should_not) - fp
        ),
        "per_check": per_check,
        "by_injected_error": by_error,
    }


def _incorrect(scenarios: Sequence[Scenario]) -> list[dict[str, Any]]:
    return [
        {
            "case": s.case,
            "injected_error": s.error,
            "expected_to_fail": list(s.expected),
            "failed": list(s.failed),
            "missed": list(s.missed),
            "false_alarms": list(s.false_alarms),
        }
        for s in scenarios
        if not s.correct
    ]


def write_results(
    result: ValidationRun, results_dir: Path, dataset: str, expectations: str
) -> dict[str, Any]:
    """Write ``validation_results.json`` and refresh the evaluation summary."""
    summary = summarise(result)
    write_json(
        results_dir / VALIDATION_RESULTS,
        {
            "run_at": dt.datetime.now().isoformat(timespec="seconds"),
            "dataset": dataset,
            "expectations": expectations,
            "summary": summary,
            "incorrect_scenarios": _incorrect(result.scenarios),
        },
    )
    write_summary(results_dir)
    return summary
