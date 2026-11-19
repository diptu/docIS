import datetime as dt
import decimal
from pathlib import Path

import pytest

from app.processing.spreadsheets import (
    build_grid,
    parse_xls,
    parse_xlsx,
    to_json_value,
)
from tests.helpers import MULTI_SHEET_XLS_BYTES, make_workbook


def write(tmp_path: Path, data: bytes, name: str) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (True, True),
        (42, 42),
        (5.0, 5),
        (1350.5, 1350.5),
        (2.0**60, 2.0**60),  # too large to be an exact int: stays float
        (float("nan"), None),
        (float("inf"), None),
        (decimal.Decimal("1.25"), 1.25),
        ("text", "text"),
        ("", None),
        ("a\x00b", "ab"),
        (dt.datetime(2026, 11, 19, 8, 30), "2026-11-19T08:30:00"),
        (dt.date(2026, 11, 19), "2026-11-19"),
        (dt.time(14, 5), "14:05:00"),
        (dt.timedelta(hours=1, minutes=30), 5400.0),
        (object, str(object)),
    ],
)
def test_to_json_value(value: object, expected: object) -> None:
    assert to_json_value(value) == expected


def test_build_grid_trims_pads_and_keeps_inner_gaps() -> None:
    rows = [
        ["a", "b", None],
        [None, None, None],  # inner gap: kept
        ["c", None, None, "d"],
        [None],  # trailing: dropped
        [],
    ]

    grid, truncated = build_grid(rows)

    assert grid == [
        ["a", "b", None, None],
        [None, None, None, None],
        ["c", None, None, "d"],
    ]
    assert truncated is False


def test_build_grid_truncates_at_row_boundary() -> None:
    rows = [["x"] * 4 for _ in range(10)]

    grid, truncated = build_grid(rows, max_cells=10)

    assert len(grid) == 2  # 8 cells; a third row would exceed 10
    assert truncated is True


def test_build_grid_empty_sheet() -> None:
    assert build_grid([[None], []]) == ([], False)


def test_parse_xlsx_reads_every_sheet_with_types(tmp_path: Path) -> None:
    workbook = make_workbook(
        {
            "Revenue": [
                ["Region", "Q1", "Q2", "Closed"],
                ["North", 1200, 1350.5, True],
                ["South", 980, 1010, False],
                [],
                ["Note: preliminary"],
            ],
            "Meta": [
                ["Generated", dt.datetime(2026, 11, 19, 9, 0)],
                ["Total", "=SUM(Revenue!B2:B3)"],
            ],
            "Internal": [["secret"]],
        },
        hidden=("Internal",),
    )

    sheets = parse_xlsx(write(tmp_path, workbook, "book.xlsx"))

    assert [(s.sheet_number, s.name, s.is_hidden) for s in sheets] == [
        (1, "Revenue", False),
        (2, "Meta", False),
        (3, "Internal", True),
    ]
    revenue, meta, _ = sheets
    assert revenue.rows == [
        ["Region", "Q1", "Q2", "Closed"],
        ["North", 1200, 1350.5, True],
        ["South", 980, 1010, False],
        [None, None, None, None],
        ["Note: preliminary", None, None, None],
    ]
    assert (revenue.row_count, revenue.column_count) == (5, 4)
    # openpyxl-written formulas have no cached result, so the value is None.
    assert meta.rows == [["Generated", "2026-11-19T09:00:00"], ["Total", None]]


def test_parse_xlsx_truncates_large_sheets(tmp_path: Path) -> None:
    rows: list[list[object]] = [[i, i * 2] for i in range(100)]
    workbook = make_workbook({"Big": rows})

    [sheet] = parse_xlsx(write(tmp_path, workbook, "big.xlsx"), max_cells=50)

    assert sheet.truncated is True
    assert sheet.row_count == 25


def test_parse_xls_reads_every_sheet_with_types(tmp_path: Path) -> None:
    sheets = parse_xls(write(tmp_path, MULTI_SHEET_XLS_BYTES, "book.xls"))

    assert [(s.name, s.is_hidden) for s in sheets] == [
        ("Revenue", False),
        ("Meta", False),
        ("Internal", True),
    ]
    revenue, meta, internal = sheets
    assert revenue.rows == [
        ["Region", "Q1", "Q2", "Closed"],
        ["North", 1200, 1350.5, True],
        ["South", 980, 1010, False],
        [None, None, None, None],
        ["Note: preliminary", None, None, None],
    ]
    # Date serial number converted; uncalculated formula is empty.
    assert meta.rows == [["Generated", "2026-11-19T00:00:00"], ["Ratio", None]]
    assert internal.rows == [["secret"]]


def test_xls_cell_conversion_edge_cases() -> None:
    import xlrd

    from app.processing.spreadsheets import _xls_cell

    def cell(ctype: int, value: object) -> xlrd.sheet.Cell:
        return xlrd.sheet.Cell(ctype, value)

    assert _xls_cell(cell(xlrd.XL_CELL_ERROR, 0x07), 0) == "#DIV/0!"
    assert _xls_cell(cell(xlrd.XL_CELL_ERROR, 0x99), 0) == "#ERROR"
    assert _xls_cell(cell(xlrd.XL_CELL_BLANK, ""), 0) is None
    # A date serial beyond the representable range is kept as the number.
    assert _xls_cell(cell(xlrd.XL_CELL_DATE, 1e12), 0) == 1e12


# ----------------------------------------------------------------- CSV

from app.processing.spreadsheets import detect_delimiter, parse_csv  # noqa: E402


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (
            b"name,amount\nalice,10\nbob,20\n",
            [["name", "amount"], ["alice", "10"], ["bob", "20"]],
        ),
        # European Excel export: semicolons, decimal commas, UTF-8 BOM, CRLF.
        (
            b"\xef\xbb\xbfKunde;Betrag\r\nJ\xc3\xbcrgen;1.250,00\r\n",
            [["Kunde", "Betrag"], ["Jürgen", "1.250,00"]],
        ),
        # Windows-1252 (Excel on Windows), tab-separated.
        (b"name\tcity\nRen\xe9\tCaf\xe9\n", [["name", "city"], ["René", "Café"]]),
        (b"a|b\n1|2\n", [["a", "b"], ["1", "2"]]),
        # Quoted fields with delimiters and newlines inside.
        (
            b'id,note\n1,"Hello, world"\n2,"two\nlines"\n',
            [["id", "note"], ["1", "Hello, world"], ["2", "two\nlines"]],
        ),
        # Values are not type-guessed: leading zeros survive.
        (b"zip,code\n01234,007\n", [["zip", "code"], ["01234", "007"]]),
        # Ragged rows are padded; empty fields and trailing blank lines dropped.
        (
            b"a,b,c\n1,,3\n4\n\n\n",
            [["a", "b", "c"], ["1", None, "3"], ["4", None, None]],
        ),
        (b"single column\nvalue\n", [["single column"], ["value"]]),
    ],
)
def test_parse_csv(tmp_path: Path, data: bytes, expected: list[list[object]]) -> None:
    [sheet] = parse_csv(write(tmp_path, data, "data.csv"), name="data")

    assert sheet.rows == expected
    assert (sheet.sheet_number, sheet.name, sheet.is_hidden) == (1, "data", False)


def test_parse_csv_truncates_large_files(tmp_path: Path) -> None:
    data = b"".join(b"%d,x\n" % i for i in range(100))

    [sheet] = parse_csv(write(tmp_path, data, "big.csv"), max_cells=20)

    assert (sheet.row_count, sheet.truncated) == (10, True)


def test_parse_csv_rejects_undecodable_text(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not valid UTF-8 or Windows-1252"):
        parse_csv(write(tmp_path, b"\x81\x8d\x8f\x90", "bad.csv"))


@pytest.mark.parametrize(
    ("sample", "expected"),
    [
        ("a;b;c\n1;2;3\n4;5;", ";"),  # last partial line ignored
        ("just one column\nanother\n", ","),  # nothing to detect: default
        # German export: decimal commas must not be mistaken for delimiters.
        ("Posten;Betrag\nBolts;6.845,38 EUR\nNuts;447,83 EUR\n", ";"),
        ('Item,Amount\nTotal,"6,845.38 GBP"\nTax,"447.83 GBP"\n', ","),
        ("a\tb\tc\n1,5\t2\t3\n", "\t"),
        ("", ","),
    ],
)
def test_detect_delimiter(sample: str, expected: str) -> None:
    assert detect_delimiter(sample) == expected
