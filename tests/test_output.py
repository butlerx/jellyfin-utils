"""One report definition has to render correctly in all four output formats."""

from __future__ import annotations

import csv
import io

import orjson
import pytest

from jellyfin_utils.output import EMPTY_CELL, OutputFormat, Report, Table, render

REPORT = Report(
    title="Library health",
    payload={"items_scanned": 2, "problems": ["missing path"]},
    summary=(("Items scanned", 2), ("Missing path", 1)),
    tables=(
        Table(
            title="Items with no on-disk path",
            columns=("Type", "Title", "Size"),
            rows=[("Movie", "Heat", 12), ("Episode", "Pilot", 3)],
            align="llr",
        ),
    ),
    notes=("Sizes are GiB.",),
)


def test_json_renders_only_the_payload() -> None:
    assert orjson.loads(render(REPORT, OutputFormat.JSON)) == REPORT.payload


def test_json_is_indented_for_human_reading() -> None:
    assert "\n  " in render(REPORT, OutputFormat.JSON)


def test_text_includes_title_summary_notes_and_a_row_count() -> None:
    lines = render(REPORT, OutputFormat.TEXT).splitlines()
    assert lines[0] == "Library health"
    assert "Items scanned: 2" in lines
    assert "Sizes are GiB." in lines
    assert "Items with no on-disk path (2)" in lines


def test_text_pads_columns_and_right_aligns_where_asked() -> None:
    lines = render(REPORT, OutputFormat.TEXT).splitlines()
    header = next(line for line in lines if line.startswith("Type"))
    rows = [line for line in lines if line.startswith(("Movie", "Episode"))]
    # Every cell in a column starts at the same offset, and "Size" is flush right.
    assert header.index("Title") == rows[0].index("Heat")
    assert header.rstrip().endswith("Size")
    assert rows[0].rstrip().endswith("12")
    assert rows[1].rstrip().endswith(" 3")


def test_text_underlines_the_header() -> None:
    lines = render(REPORT, OutputFormat.TEXT).splitlines()
    header_index = next(index for index, line in enumerate(lines) if line.startswith("Type"))
    assert set(lines[header_index + 1]) == {"-"}


def test_markdown_uses_headings_and_an_alignment_row() -> None:
    lines = render(REPORT, OutputFormat.MARKDOWN).splitlines()
    assert lines[0] == "# Library health"
    assert "- **Items scanned:** 2" in lines
    assert "## Items with no on-disk path" in lines
    assert "| Type | Title | Size |" in lines
    assert "| --- | --- | ---: |" in lines


def test_markdown_escapes_pipes_and_newlines_in_cells() -> None:
    report = Report(
        title="T",
        payload={},
        tables=(Table(columns=("Path",), rows=[("a|b\nc",)]),),
    )
    assert r"| a\|b c |" in render(report, OutputFormat.MARKDOWN)


def test_csv_is_parseable_and_carries_the_header() -> None:
    rows = list(csv.reader(io.StringIO(render(REPORT, OutputFormat.CSV))))
    assert rows[0] == ["Type", "Title", "Size"]
    assert rows[1] == ["Movie", "Heat", "12"]


def test_csv_labels_and_separates_multiple_tables() -> None:
    report = Report(
        title="T",
        payload={},
        tables=(
            Table(title="First", columns=("A",), rows=[("1",)]),
            Table(title="Second", columns=("B",), rows=[("2",)]),
        ),
    )
    rows = list(csv.reader(io.StringIO(render(report, OutputFormat.CSV))))
    # A blank row separates the two blocks, each led by its title then its header.
    assert rows == [["First"], ["A"], ["1"], [], ["Second"], ["B"], ["2"]]


def test_csv_falls_back_to_summary_when_a_report_has_no_tables() -> None:
    report = Report(title="Created user", payload={}, summary=(("ID", "abc"),))
    rows = list(csv.reader(io.StringIO(render(report, OutputFormat.CSV))))
    assert rows == [["Field", "Value"], ["Result", "Created user"], ["ID", "abc"]]


def test_empty_table_shows_its_own_message_in_text_and_markdown() -> None:
    report = Report(
        title="T",
        payload={},
        tables=(Table(columns=("A",), rows=[], empty="Nothing to clean up."),),
    )
    assert "Nothing to clean up." in render(report, OutputFormat.TEXT)
    assert "_Nothing to clean up._" in render(report, OutputFormat.MARKDOWN)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, EMPTY_CELL),
        (True, "yes"),
        (False, "no"),
        (["b", "a"], "b, a"),
        ({"b", "a"}, "a, b"),
        ((), EMPTY_CELL),
        ("", EMPTY_CELL),
        (0, "0"),
    ],
)
def test_text_cells_cover_every_value_shape(value: object, expected: str) -> None:
    report = Report(title="T", payload={}, tables=(Table(columns=("A",), rows=[(value,)]),))
    assert render(report, OutputFormat.TEXT).splitlines()[-1].strip() == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, ""), (True, "yes"), (["a", "b"], "a, b"), ({"b", "a"}, "a, b"), ("", "")],
)
def test_csv_cells_leave_missing_values_blank(value: object, expected: str) -> None:
    report = Report(title="T", payload={}, tables=(Table(columns=("A",), rows=[(value,)]),))
    rows = list(csv.reader(io.StringIO(render(report, OutputFormat.CSV))))
    assert rows[1] == [expected]


def test_every_format_renders_a_report_with_no_tables_or_summary() -> None:
    report = Report(title="Nothing to report", payload={})
    for output_format in OutputFormat:
        assert render(report, output_format)
