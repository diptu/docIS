"""
Structured parsing of spreadsheets: Excel workbooks (XLSX, legacy XLS) and
CSV files.

Every worksheet becomes an ``ExtractedSheet``: a rectangular grid of
JSON-safe cell values. Trailing empty rows and columns are dropped; gaps
inside the data are kept so row/column positions stay meaningful.

All functions are blocking; call them from a worker thread.
"""

import csv
import datetime as dt
import decimal
import io
import math
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import openpyxl
import xlrd

from app.processing.progress import ProgressCallback, Stage, ignore_progress

CellValue = str | int | float | bool | None

# Upper bound on stored cells per sheet; larger sheets are cut off at a row
# boundary and flagged ``truncated``.
MAX_CELLS_PER_SHEET = 1_000_000
# CSV: delimiters considered during detection, how much text to sniff, and
# encodings tried in order (matching upload validation).
CSV_DELIMITERS = ",;\t|"
CSV_SNIFF_BYTES = 64 * 1024
CSV_ENCODINGS = ("utf-8-sig", "cp1252")
# Floats that are whole numbers within this range are stored as ints.
_MAX_SAFE_INT = 2**53


@dataclass(frozen=True)
class ExtractedSheet:
    """One worksheet as a grid of cell values."""

    sheet_number: int
    name: str
    rows: list[list[CellValue]]
    is_hidden: bool = False
    truncated: bool = False

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def column_count(self) -> int:
        return len(self.rows[0]) if self.rows else 0


def to_json_value(value: object) -> CellValue:
    """Convert a spreadsheet cell value to a JSON-safe value.

    Dates and times become ISO 8601 strings, whole floats become ints,
    non-finite numbers and empty strings become None, and NUL characters
    (which PostgreSQL JSONB cannot store) are removed.
    """
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, str):
        cleaned = value.replace("\x00", "")
        return cleaned if cleaned != "" else None
    if isinstance(value, decimal.Decimal):
        value = float(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        if value.is_integer() and abs(value) < _MAX_SAFE_INT:
            return int(value)
        return value
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return value.total_seconds()
    return str(value)


def build_grid(
    rows: Iterable[Iterable[object]], max_cells: int = MAX_CELLS_PER_SHEET
) -> tuple[list[list[CellValue]], bool]:
    """Turn raw rows into a trimmed, rectangular grid.

    Returns the grid and whether it was truncated at ``max_cells``.
    """
    grid: list[list[CellValue]] = []
    pending_blank = 0  # empty rows seen since the last non-empty row
    cells = 0
    truncated = False

    for raw in rows:
        row = [to_json_value(value) for value in raw]
        while row and row[-1] is None:
            row.pop()
        if not row:
            pending_blank += 1
            continue
        if cells + len(row) > max_cells:
            truncated = True
            break
        grid.extend([] for _ in range(pending_blank))
        pending_blank = 0
        grid.append(row)
        cells += len(row)

    width = max((len(row) for row in grid), default=0)
    for row in grid:
        row.extend([None] * (width - len(row)))
    return grid, truncated


def parse_xlsx(
    path: Path,
    max_cells: int = MAX_CELLS_PER_SHEET,
    progress: ProgressCallback = ignore_progress,
) -> list[ExtractedSheet]:
    """Parse every worksheet of an XLSX workbook.

    Formula cells yield the value Excel last calculated and saved; files
    written by tools that never calculate formulas have no such value, and
    those cells come back as None.
    """
    with path.open("rb") as file:
        workbook = openpyxl.load_workbook(file, read_only=True, data_only=True)
        try:
            total = len(workbook.worksheets)
            progress(Stage.PARSING_SHEETS, 0, total)
            sheets = []
            for number, sheet in enumerate(workbook.worksheets, start=1):
                # The stored dimension is often wrong in files from non-Excel
                # writers; resetting makes iter_rows read every row.
                sheet.reset_dimensions()
                grid, truncated = build_grid(
                    sheet.iter_rows(values_only=True), max_cells
                )
                sheets.append(
                    ExtractedSheet(
                        sheet_number=number,
                        name=sheet.title,
                        rows=grid,
                        is_hidden=sheet.sheet_state != "visible",
                        truncated=truncated,
                    )
                )
                progress(Stage.PARSING_SHEETS, number, total)
            return sheets
        finally:
            workbook.close()


def _xls_cell(cell: xlrd.sheet.Cell, datemode: int) -> object:
    if cell.ctype == xlrd.XL_CELL_DATE:
        try:
            return xlrd.xldate_as_datetime(cell.value, datemode)
        except (xlrd.xldate.XLDateError, ValueError, OverflowError):
            return cell.value
    if cell.ctype == xlrd.XL_CELL_BOOLEAN:
        return bool(cell.value)
    if cell.ctype == xlrd.XL_CELL_ERROR:
        return xlrd.error_text_from_code.get(cell.value, "#ERROR")
    if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
        return None
    return cell.value


def _xls_rows(sheet: xlrd.sheet.Sheet, datemode: int) -> Iterator[list[object]]:
    for index in range(sheet.nrows):
        yield [_xls_cell(cell, datemode) for cell in sheet.row(index)]


def parse_xls(
    path: Path,
    max_cells: int = MAX_CELLS_PER_SHEET,
    progress: ProgressCallback = ignore_progress,
) -> list[ExtractedSheet]:
    """Parse every worksheet of a legacy XLS workbook."""
    book = xlrd.open_workbook(str(path), logfile=io.StringIO(), on_demand=True)
    try:
        progress(Stage.PARSING_SHEETS, 0, book.nsheets)
        sheets = []
        for index in range(book.nsheets):
            sheet = book.sheet_by_index(index)
            grid, truncated = build_grid(_xls_rows(sheet, book.datemode), max_cells)
            sheets.append(
                ExtractedSheet(
                    sheet_number=index + 1,
                    name=sheet.name,
                    rows=grid,
                    is_hidden=sheet.visibility != 0,
                    truncated=truncated,
                )
            )
            book.unload_sheet(index)
            progress(Stage.PARSING_SHEETS, index + 1, book.nsheets)
        return sheets
    finally:
        book.release_resources()


def detect_delimiter(sample: str) -> str:
    """Guess the delimiter from a text sample; defaults to a comma.

    Only complete lines are sniffed, and only common delimiters are
    considered, so prose with stray punctuation cannot pick an odd one.
    """
    if "\n" in sample:
        sample = sample[: sample.rindex("\n")]
    try:
        return csv.Sniffer().sniff(sample, delimiters=CSV_DELIMITERS).delimiter
    except csv.Error:
        return ","


def _read_csv(
    path: Path, encoding: str, max_cells: int
) -> tuple[list[list[CellValue]], bool]:
    with path.open(encoding=encoding, newline="") as file:
        delimiter = detect_delimiter(file.read(CSV_SNIFF_BYTES))
        file.seek(0)
        return build_grid(csv.reader(file, delimiter=delimiter), max_cells)


def parse_csv(
    path: Path,
    name: str = "Sheet1",
    max_cells: int = MAX_CELLS_PER_SHEET,
    progress: ProgressCallback = ignore_progress,
) -> list[ExtractedSheet]:
    """Parse a CSV file into a single sheet.

    The encoding is UTF-8 (BOM allowed) or else Windows-1252, as accepted at
    upload. Values are kept as strings, because CSV has no types and
    guessing would corrupt values such as ``"01234"`` or ``"1.250,00"``.
    Empty fields become None.
    """
    progress(Stage.PARSING_CSV, 0, 1)
    for encoding in CSV_ENCODINGS:
        try:
            grid, truncated = _read_csv(path, encoding, max_cells)
        except UnicodeDecodeError:
            continue
        progress(Stage.PARSING_CSV, 1, 1)
        return [
            ExtractedSheet(sheet_number=1, name=name, rows=grid, truncated=truncated)
        ]
    raise ValueError("CSV is not valid UTF-8 or Windows-1252 text.")
