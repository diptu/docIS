"""
OCR benchmark on poor-quality images: how well text is read from skewed,
blurred, low-resolution, dark and noisy photos, and how much each image
clean-up step adds.

Three measurements, all OCR only (no Claude calls, no cost):

* **Severity sweeps** (synthetic): one defect at a time (skew, blur, low
  resolution, darkness, grain, darkness plus grain), each at increasing
  severity, read with and without clean-up.
* **Stage ablation** (synthetic): combined defects read once per clean-up
  stage (below), showing what each step adds.
* **Real documents** (optional, ``--dataset``): the labelled third-party
  photos and scans, grouped by defect, scored on the invoice number, the
  totals and every line amount found in the OCR text.

Synthetic invoice pages are degraded like phone photos and poor scans
(dark, grainy, shadowed, blurred, low-resolution, tilted, JPEG-compressed)
and read with Tesseract through the production ``ocr_image``. Each page is
read once per *stage*; the stages switch the steps of
``app.processing.image_prep`` on one after another:

    raw            no clean-up (enhance_images=False)
    illumination   background flattening and contrast stretch only
    + denoise      ... plus median filtering of grainy images
    + paper white  ... plus pushing paper grain to white
    full           ... plus deskew (what production runs)

Accuracy is the share of key invoice values (number, date, supplier,
amounts) found verbatim in the OCR text; confidence is Tesseract's mean
word confidence. Two layouts are measured: a *sparse* page (a few lines,
like a receipt or short invoice, where text is under 1% of the pixels)
and a *dense* one (header plus line items).

    uv run python -m evaluation ocr                      # synthetic only
    uv run python -m evaluation ocr --dataset evaluation/third_party_eval/dataset \
        --out evaluation/third_party_eval/results       # plus real documents

Needs Tesseract; costs nothing. Results: <out>/ocr_preprocessing.json; the
HTML evaluation report includes them.
"""

import io
import json
import re
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from unittest import mock

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.processing import image_prep
from app.processing.extract import extract_content
from app.processing.ocr import OcrConfig, ocr_image
from evaluation.reporting import OCR_RESULTS, RESULTS_DIR, write_json

HEADER = [
    "INVOICE No INV-2026-0042",
    "Date 2026-11-20",
    "Supplier Acme Supplies Ltd",
    "Subtotal 1000.00",
    "Tax 150.00",
    "Total 1150.00",
]
KEY_VALUES = ["INV-2026-0042", "2026-11-20", "Acme", "1000.00", "150.00", "1150.00"]
LINE_ITEMS = [
    f"Item {i} Widget model X{i} qty {i + 1} price {i * 7}.50" for i in range(12)
]
LAYOUTS = {"sparse": HEADER, "dense": HEADER + [""] + LINE_ITEMS}
PAGE_SIZE = (1700, 2200)  # US Letter at 200 DPI


@dataclass(frozen=True)
class PhotoDefects:
    """How a clean page is spoiled. Defaults leave it untouched."""

    scale: float = 1.0  # resolution relative to 200 DPI
    brightness: float = 1.0  # paper brightness, 1 = white
    grain: float = 0.0  # Gaussian noise, standard deviation in 8-bit levels
    shadow: bool = False  # light falls off to 45% across the page
    tilt: float = 0.0  # degrees counter-clockwise
    blur: float = 0.0  # Gaussian blur radius (out of focus)
    jpeg_quality: int = 90


CASES: dict[str, PhotoDefects] = {
    "clean": PhotoDefects(),
    "low resolution": PhotoDefects(scale=0.5, jpeg_quality=75),
    "tilted 4°": PhotoDefects(tilt=4, jpeg_quality=75),
    "blurred": PhotoDefects(blur=1.5, jpeg_quality=85),
    "shadow": PhotoDefects(shadow=True, grain=3, jpeg_quality=70),
    "dark": PhotoDefects(brightness=0.6, grain=3, jpeg_quality=70),
    "dark, grainy": PhotoDefects(brightness=0.6, grain=10, jpeg_quality=60),
    "dark, very grainy": PhotoDefects(brightness=0.5, grain=18, jpeg_quality=50),
    "very dark": PhotoDefects(brightness=0.35, grain=8, jpeg_quality=60),
    "grainy, white paper": PhotoDefects(grain=25, jpeg_quality=60),
    "shadow, grainy, tilted": PhotoDefects(
        brightness=0.8, grain=15, shadow=True, tilt=2, jpeg_quality=55
    ),
    "phone photo": PhotoDefects(
        scale=0.6, brightness=0.75, grain=12, shadow=True, tilt=2, jpeg_quality=50
    ),
    "dark, grainy, low-res, tilted": PhotoDefects(
        scale=0.5, brightness=0.6, grain=10, tilt=3, jpeg_quality=60
    ),
    "harsh: tiny, dark, grainy": PhotoDefects(
        scale=0.5, brightness=0.55, grain=18, jpeg_quality=40
    ),
    "harsh: tiny, tilted, q40": PhotoDefects(
        scale=0.41, brightness=0.55, grain=18, tilt=4, jpeg_quality=40
    ),
}


# One defect at a time, at increasing severity. Every page is saved as a
# JPEG (quality 75) like a phone photo; "dark" pages carry the slight
# sensor grain of a real low-light photo.
SWEEPS: dict[str, list[tuple[str, PhotoDefects]]] = {
    "Skew": [
        ("2°", PhotoDefects(tilt=2, jpeg_quality=75)),
        ("5°", PhotoDefects(tilt=5, jpeg_quality=75)),
        ("10°", PhotoDefects(tilt=10, jpeg_quality=75)),
        ("15°", PhotoDefects(tilt=15, jpeg_quality=75)),
    ],
    "Blur": [
        ("radius 1", PhotoDefects(blur=1, jpeg_quality=75)),
        ("radius 2", PhotoDefects(blur=2, jpeg_quality=75)),
        ("radius 3", PhotoDefects(blur=3, jpeg_quality=75)),
    ],
    "Low resolution": [
        ("100 DPI", PhotoDefects(scale=0.5, jpeg_quality=75)),
        ("70 DPI", PhotoDefects(scale=0.35, jpeg_quality=75)),
        ("50 DPI", PhotoDefects(scale=0.25, jpeg_quality=75)),
    ],
    "Dark": [
        ("60% light", PhotoDefects(brightness=0.6, grain=3, jpeg_quality=75)),
        ("40% light", PhotoDefects(brightness=0.4, grain=3, jpeg_quality=75)),
        ("25% light", PhotoDefects(brightness=0.25, grain=3, jpeg_quality=75)),
    ],
    "Noisy": [
        ("grain σ10", PhotoDefects(grain=10, jpeg_quality=75)),
        ("grain σ20", PhotoDefects(grain=20, jpeg_quality=75)),
        ("grain σ35", PhotoDefects(grain=35, jpeg_quality=75)),
    ],
    "Dark + noisy": [
        ("60% light, σ10", PhotoDefects(brightness=0.6, grain=10, jpeg_quality=75)),
        ("45% light, σ18", PhotoDefects(brightness=0.45, grain=18, jpeg_quality=75)),
        ("35% light, σ25", PhotoDefects(brightness=0.35, grain=25, jpeg_quality=75)),
    ],
}

# Real documents (third-party dataset), grouped by defect: file-name
# prefixes; each comes in mild, moderate and severe versions.
DOCUMENT_FAMILIES: dict[str, tuple[str, ...]] = {
    "Skew": ("photo-rotate-small", "photo-orientation"),
    "Blur": ("photo-defocus-blur", "photo-motion-blur"),
    "Low resolution": ("photo-low-res", "photo-jpeg"),
    "Dark": ("photo-low-light", "photo-shadow"),
    "Noisy": ("scan-noise", "scan-fax"),
}
# Documents that go through OCR, by category (file-name prefix).
OCR_CATEGORIES = ("photo", "scan", "handwritten")
SEVERITIES = ("mild", "moderate", "severe")
_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".pdf": "application/pdf",
}


def make_page(lines: list[str], size: tuple[int, int] = PAGE_SIZE) -> Image.Image:
    """Black text on white paper, grayscale."""
    image = Image.new("L", size, 255)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=36)
    for i, line in enumerate(lines):
        draw.text((120, 150 + i * 60), line, fill=0, font=font)
    return image


def degrade(page: Image.Image, defects: PhotoDefects, seed: int = 0) -> Image.Image:
    """Apply ``defects`` to a page, ending with a JPEG round trip."""
    image = page.convert("L")
    if defects.tilt:
        image = image.rotate(
            defects.tilt, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=255
        )
    if defects.scale != 1.0:
        image = image.resize(
            (round(image.width * defects.scale), round(image.height * defects.scale)),
            Image.Resampling.LANCZOS,
        )
    if defects.blur:
        image = image.filter(ImageFilter.GaussianBlur(defects.blur))
    pixels = np.asarray(image, dtype=np.float64)
    if defects.shadow:
        pixels = pixels * np.linspace(1.0, 0.45, image.width)[None, :]
    pixels = pixels * defects.brightness
    if defects.grain:
        rng = np.random.default_rng(seed)
        pixels = pixels + rng.normal(0, defects.grain, pixels.shape)
    image = Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8), mode="L")
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=defects.jpeg_quality)
    return Image.open(io.BytesIO(buffer.getvalue())).convert("L")


def _identity(image: Image.Image) -> Image.Image:
    return image


# Stage name -> image_prep steps switched off (None: no clean-up at all).
STAGES: dict[str, tuple[str, ...] | None] = {
    "raw": None,
    "illumination": ("reduce_noise", "whiten_paper", "deskew"),
    "+ denoise": ("whiten_paper", "deskew"),
    "+ paper white": ("deskew",),
    "full": (),
}


@contextmanager
def _stage(disabled: tuple[str, ...] | None) -> Iterator[OcrConfig]:
    config = OcrConfig(enhance_images=disabled is not None)
    with ExitStack() as stack:
        for step in disabled or ():
            stack.enter_context(mock.patch.object(image_prep, step, _identity))
        yield config


@dataclass(frozen=True)
class Reading:
    found: int
    total: int
    confidence: float | None
    seconds: float


def read(image: Image.Image, config: OcrConfig) -> Reading:
    started = time.perf_counter()
    result = ocr_image(image, config)
    found = sum(value in result.text for value in KEY_VALUES)
    return Reading(
        found, len(KEY_VALUES), result.confidence, time.perf_counter() - started
    )


def _row(reading: Reading) -> dict[str, Any]:
    return asdict(reading)


def run_ablation(
    layouts: list[str],
    cases: dict[str, PhotoDefects] = CASES,
    stages: dict[str, tuple[str, ...] | None] = STAGES,
) -> dict[str, Any]:
    """Read every (layout, case) once per stage."""
    rows = []
    for layout in layouts:
        page = make_page(LAYOUTS[layout])
        for case, defects in cases.items():
            image = degrade(page, defects)
            readings = {}
            for stage, disabled in stages.items():
                with _stage(disabled) as config:
                    readings[stage] = _row(read(image, config))
            rows.append(
                {
                    "layout": layout,
                    "case": case,
                    "defects": asdict(defects),
                    "stages": readings,
                }
            )
    return {"stages": list(stages), "rows": rows, "summary": summarize(rows, stages)}


SWEEP_STAGES: dict[str, tuple[str, ...] | None] = {"raw": None, "full": ()}


def run_sweeps(
    layouts: list[str],
    sweeps: dict[str, list[tuple[str, PhotoDefects]]] = SWEEPS,
) -> dict[str, Any]:
    """Read every defect at every severity, without and with clean-up."""
    rows = []
    for layout in layouts:
        page = make_page(LAYOUTS[layout])
        for defect, levels in sweeps.items():
            for severity, defects in levels:
                image = degrade(page, defects)
                readings = {}
                for stage, disabled in SWEEP_STAGES.items():
                    with _stage(disabled) as config:
                        readings[stage] = _row(read(image, config))
                rows.append(
                    {
                        "layout": layout,
                        "defect": defect,
                        "severity": severity,
                        "defects": asdict(defects),
                        "stages": readings,
                    }
                )
    return {"stages": list(SWEEP_STAGES), "rows": rows}


def summarize(
    rows: list[dict[str, Any]], stages: dict[str, tuple[str, ...] | None]
) -> dict[str, Any]:
    """Accuracy, mean confidence, pages fully read and time per layout and stage."""
    summary: dict[str, Any] = {}
    for layout in sorted({row["layout"] for row in rows}):
        mine = [row for row in rows if row["layout"] == layout]
        summary[layout] = {
            stage: _totals([row["stages"][stage] for row in mine]) for stage in stages
        }
    return summary


def _totals(readings: list[dict[str, Any]]) -> dict[str, Any]:
    found = sum(r["found"] for r in readings)
    total = sum(r["total"] for r in readings)
    confidences = [r["confidence"] or 0.0 for r in readings]
    return {
        "accuracy": round(found / total, 4) if total else None,
        "values_found": found,
        "values_total": total,
        "pages_fully_read": sum(r["found"] == r["total"] for r in readings),
        "pages": len(readings),
        "mean_confidence": (
            round(sum(confidences) / len(confidences), 1) if confidences else None
        ),
        "seconds": round(sum(r["seconds"] for r in readings), 2),
    }


# --- Real documents ----------------------------------------------------------

_NUMBER = re.compile(r"\d{1,3}(?:[ \u00a0\u202f']\d{3})+(?:[.,]\d+)?|\d[\d.,]*")


def _to_decimal(token: str) -> Decimal | None:
    """Read a printed amount in any common locale: 1,234.56 / 1.234,56 /
    1 234,56 / 1'234.56 / 1234.56. A lone separator followed by exactly
    three digits is a thousands separator, otherwise a decimal one."""
    token = re.sub(r"[ \u00a0\u202f']", "", token).strip(".,")
    if not token:
        return None
    if "," in token and "." in token:
        decimal = "," if token.rfind(",") > token.rfind(".") else "."
    elif token.count(",") == 1 and len(token.split(",")[1]) != 3:
        decimal = ","
    elif token.count(".") == 1 and len(token.split(".")[1]) != 3:
        decimal = "."
    else:
        decimal = ""
    thousands = {",", "."} - {decimal}
    for mark in thousands:
        token = token.replace(mark, "")
    if decimal:
        token = token.replace(decimal, ".")
    try:
        return Decimal(token)
    except InvalidOperation:
        return None


def amounts_in(text: str) -> set[Decimal]:
    """Every number printed in ``text``, as values rounded to cents."""
    found = set()
    for match in _NUMBER.finditer(text):
        value = _to_decimal(match.group())
        if value is not None:
            found.add(value.quantize(Decimal("0.01")))
    return found


def expected_values(ground_truth: dict[str, Any]) -> tuple[str | None, list[Decimal]]:
    """The invoice number and the amounts (subtotal, tax, total, every line
    amount) a reader must find in the text."""
    expected = ground_truth.get("expected", ground_truth)
    amounts = [
        expected.get("subtotal"),
        expected.get("tax_amount"),
        expected.get("total_amount"),
        *(line.get("amount") for line in expected.get("line_items") or []),
    ]
    return expected.get("invoice_number"), [
        abs(Decimal(str(a))).quantize(Decimal("0.01")) for a in amounts if a is not None
    ]


def score_text(text: str, ground_truth: dict[str, Any]) -> tuple[int, int]:
    """(values found, values expected) in an OCR text."""
    number, amounts = expected_values(ground_truth)
    printed = {abs(a) for a in amounts_in(text)}
    found = sum(amount in printed for amount in amounts)
    total = len(amounts)
    if number:
        total += 1
        found += number in text
    return found, total


def _family(name: str) -> str | None:
    for family, prefixes in DOCUMENT_FAMILIES.items():
        if any(name.startswith(prefix + "-") for prefix in prefixes):
            return family
    return None


def run_documents(
    dataset: Path, stages: dict[str, OcrConfig] | None = None
) -> dict[str, Any]:
    """OCR every photographed, scanned and handwritten document of a labelled
    dataset (``documents/`` + ``ground_truth/``) through the production
    extractor, once per stage, and score the text."""
    stages = stages or {
        "raw": OcrConfig(enhance_images=False),
        "full": OcrConfig(),
    }
    rows = []
    for path in sorted((dataset / "documents").iterdir()):
        category = path.name.split("-", 1)[0]
        media_type = _MEDIA_TYPES.get(path.suffix.lower())
        truth_path = dataset / "ground_truth" / f"{path.stem}.json"
        if category not in OCR_CATEGORIES or not media_type or not truth_path.exists():
            continue
        truth = json.loads(truth_path.read_text())
        readings = {}
        for stage, config in stages.items():
            started = time.perf_counter()
            pages = extract_content(path, media_type, config, path.name).pages
            seconds = time.perf_counter() - started
            text = "\n".join(page.text for page in pages)
            confidences = [p.confidence for p in pages if p.confidence is not None]
            found, total = score_text(text, truth)
            readings[stage] = _row(
                Reading(
                    found,
                    total,
                    (
                        round(sum(confidences) / len(confidences), 1)
                        if confidences
                        else None
                    ),
                    seconds,
                )
            )
        severity = next((s for s in SEVERITIES if path.stem.endswith(s)), None)
        rows.append(
            {
                "document": path.name,
                "category": category,
                "family": _family(path.stem),
                "severity": severity,
                "stages": readings,
            }
        )
    return {"dataset": str(dataset), "stages": list(stages), "rows": rows}


# --- Running and reporting -----------------------------------------------------


def run(layouts: list[str], dataset: Path | None = None) -> dict[str, Any]:
    """All measurements, as JSON data."""
    results: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "sweeps": run_sweeps(layouts),
        "ablation": run_ablation(layouts),
    }
    if dataset is not None:
        results["documents"] = run_documents(dataset)
    return results


def _cell(reading: dict[str, Any]) -> str:
    return f"{reading['found']}/{reading['total']} ({reading['confidence'] or 0:.0f})"


def _pct(totals: dict[str, Any]) -> str:
    accuracy = totals["accuracy"]
    return "n/a" if accuracy is None else f"{accuracy:.0%}"


def sweep_table(sweeps: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per (defect, severity): accuracy without and with clean-up,
    sparse and dense layouts pooled."""
    table: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = {}
    for row in sweeps["rows"]:
        entry = table.setdefault((row["defect"], row["severity"]), {})
        for stage, reading in row["stages"].items():
            entry.setdefault(stage, []).append(reading)
    return [
        {
            "defect": defect,
            "severity": severity,
            **{stage: _totals(readings) for stage, readings in stages.items()},
        }
        for (defect, severity), stages in table.items()
    ]


def document_table(documents: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per defect family and severity, plus the OCR categories."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in documents["rows"]:
        if row["family"]:
            groups.setdefault((row["family"], row["severity"] or "-"), []).append(row)
            groups.setdefault((row["family"], "all"), []).append(row)
        groups.setdefault(("All " + row["category"], "all"), []).append(row)
        groups.setdefault(("All OCR documents", "all"), []).append(row)
    order = list(DOCUMENT_FAMILIES) + [f"All {c}" for c in OCR_CATEGORIES]
    order.append("All OCR documents")
    severity_order = {s: i for i, s in enumerate((*SEVERITIES, "all"))}
    keys = sorted(
        groups,
        key=lambda k: (order.index(k[0]), severity_order.get(k[1], 9)),
    )
    return [
        {
            "group": group,
            "severity": severity,
            "documents": len(groups[(group, severity)]),
            **{
                stage: _totals([r["stages"][stage] for r in groups[(group, severity)]])
                for stage in documents["stages"]
            },
        }
        for group, severity in keys
    ]


def render_markdown(results: dict[str, Any]) -> str:
    out = ["## Severity sweeps (synthetic; sparse and dense pages pooled)\n"]
    out.append("| defect | severity | no clean-up | with clean-up | confidence |")
    out.append("|---|---|---|---|---|")
    for row in sweep_table(results["sweeps"]):
        out.append(
            f"| {row['defect']} | {row['severity']} | {_pct(row['raw'])} "
            f"| **{_pct(row['full'])}** "
            f"| {row['raw']['mean_confidence']:.0f} → "
            f"{row['full']['mean_confidence']:.0f} |"
        )
    documents = results.get("documents")
    if documents:
        out.append("\n## Real documents (third-party dataset)\n")
        out.append("| group | severity | docs | no clean-up | with clean-up |")
        out.append("|---|---|---|---|---|")
        for row in document_table(documents):
            out.append(
                f"| {row['group']} | {row['severity']} | {row['documents']} "
                f"| {_pct(row['raw'])} | **{_pct(row['full'])}** |"
            )
    ablation = results["ablation"]
    stages = ablation["stages"]
    for layout, totals in ablation["summary"].items():
        out.append(f"\n## Stage ablation, {layout} page\n")
        out.append("| case | " + " | ".join(stages) + " |")
        out.append("|---|" + "---|" * len(stages))
        for row in ablation["rows"]:
            if row["layout"] == layout:
                cells = " | ".join(_cell(row["stages"][s]) for s in stages)
                out.append(f"| {row['case']} | {cells} |")
        out.append(
            "| **accuracy** | "
            + " | ".join(f"**{_pct(totals[s])}**" for s in stages)
            + " |"
        )
    out.append(
        "\nCells: values found / expected (mean Tesseract word confidence). "
        "Ablation stages are cumulative; `full` is what production runs."
    )
    return "\n".join(out)


def write_results(results: dict[str, Any], out: Path = RESULTS_DIR) -> Path:
    path = out / OCR_RESULTS
    write_json(path, results)
    return path
