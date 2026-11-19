"""Each extractor reports progress steps through the callback."""

from pathlib import Path

import pytest

from app.processing.extract import CSV_TYPE, XLS_TYPE, XLSX_TYPE, extract_content
from app.processing.ocr import OcrConfig
from app.processing.progress import ProgressTracker, ignore_progress
from tests.helpers import (
    CSV_BYTES,
    MULTI_SHEET_XLS_BYTES,
    concat_pdfs,
    encode,
    make_scanned_text_pdf,
    make_text_image,
    make_text_pdf,
    make_workbook,
    requires_tesseract,
)

Steps = list[tuple[str, int, int | None]]


def run(tmp_path: Path, data: bytes, media_type: str) -> Steps:
    path = tmp_path / "file"
    path.write_bytes(data)
    steps: Steps = []
    extract_content(
        path,
        media_type,
        OcrConfig(),
        "file.csv",
        lambda stage, current, total: steps.append((stage, current, total)),
    )
    return steps


@requires_tesseract
def test_pdf_reports_text_then_ocr(tmp_path: Path) -> None:
    pdf = concat_pdfs(
        make_text_pdf("A digital page with enough text"),
        make_scanned_text_pdf(["Scanned page"]),
    )

    assert run(tmp_path, pdf, "application/pdf") == [
        ("extracting_text", 0, 2),
        ("extracting_text", 1, 2),
        ("extracting_text", 2, 2),
        ("ocr", 0, 1),
        ("ocr", 1, 1),
    ]


@requires_tesseract
def test_image_reports_each_frame(tmp_path: Path) -> None:
    frames = [make_text_image(["one"]), make_text_image(["two"])]
    tiff = encode(frames[0], "TIFF", save_all=True, append_images=frames[1:])

    assert run(tmp_path, tiff, "image/tiff") == [
        ("ocr", 0, 2),
        ("ocr", 1, 2),
        ("ocr", 2, 2),
    ]


@pytest.mark.parametrize(
    ("data", "media_type", "expected"),
    [
        (
            make_workbook({"A": [["x"]], "B": [["y"]]}),
            XLSX_TYPE,
            [
                ("parsing_sheets", 0, 2),
                ("parsing_sheets", 1, 2),
                ("parsing_sheets", 2, 2),
            ],
        ),
        (
            MULTI_SHEET_XLS_BYTES,
            XLS_TYPE,
            [("parsing_sheets", i, 3) for i in range(4)],
        ),
        (CSV_BYTES, CSV_TYPE, [("parsing_csv", 0, 1), ("parsing_csv", 1, 1)]),
    ],
)
def test_spreadsheets_report_each_sheet(
    tmp_path: Path, data: bytes, media_type: str, expected: Steps
) -> None:
    assert run(tmp_path, data, media_type) == expected


def test_tracker_keeps_latest_update_and_counts_versions() -> None:
    tracker = ProgressTracker()
    assert tracker.snapshot().version == 0

    tracker.update("ocr", 1, 4)
    tracker.update("ocr", 2, 4)

    snapshot = tracker.snapshot()
    assert (snapshot.stage, snapshot.current, snapshot.total) == ("ocr", 2, 4)
    assert snapshot.version == 2


def test_ignore_progress_accepts_anything() -> None:
    ignore_progress("anything", 1, None)
