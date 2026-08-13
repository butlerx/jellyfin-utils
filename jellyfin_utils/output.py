"""
Shared output formatting for every jellyfin-utils command.

A command builds a :class:`Report` — a title, key/value summary facts, optional
legend notes, and zero or more :class:`Table` blocks — alongside the payload it
wants ``--output json`` to emit. :func:`emit` renders that as text, JSON, CSV,
or Markdown, so every command supports every format without writing four
renderers by hand.

The JSON payload is kept separate from the tables because JSON output stays
nested (groups, per-type maps) while the other three formats are flat.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

import click
import orjson

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Any

COLUMN_GAP = "  "
EMPTY_CELL = "—"


class OutputFormat(StrEnum):
    """Formats every command can render its report in."""

    TEXT = "text"
    JSON = "json"
    CSV = "csv"
    MARKDOWN = "markdown"


@dataclass(frozen=True)
class Table:
    """
    Rows sharing one set of columns, rendered as a table or a CSV block.

    ``align`` holds one character per column, ``"r"`` for right-aligned and
    anything else for left; missing entries default to left.
    """

    columns: Sequence[str]
    rows: Sequence[Sequence[Any]]
    title: str | None = None
    align: str = ""
    empty: str = "None."


@dataclass(frozen=True)
class Report:
    """One command's output, independent of the format it is rendered in."""

    title: str
    payload: dict[str, Any]
    summary: Sequence[tuple[str, Any]] = ()
    tables: Sequence[Table] = ()
    notes: Sequence[str] = field(default=())


def emit(report: Report, output_format: OutputFormat) -> None:
    """Print a report in the requested format."""
    # The CSV writer already terminates its last row.
    click.echo(render(report, output_format), nl=output_format is not OutputFormat.CSV)


def render(report: Report, output_format: OutputFormat) -> str:
    """Render a report as text, JSON, CSV, or Markdown."""
    match output_format:
        case OutputFormat.JSON:
            return orjson.dumps(report.payload, option=orjson.OPT_INDENT_2).decode()
        case OutputFormat.CSV:
            return _render_csv(report)
        case OutputFormat.MARKDOWN:
            return _render_markdown(report)
        case _:
            return _render_text(report)


def _text_cell(value: Any) -> str:  # noqa: ANN401
    if value is None:
        return EMPTY_CELL
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, set | frozenset):
        return ", ".join(str(item) for item in sorted(value, key=str)) or EMPTY_CELL
    if isinstance(value, list | tuple):
        return ", ".join(str(item) for item in value) or EMPTY_CELL
    return str(value) or EMPTY_CELL


def _csv_cell(value: Any) -> str:  # noqa: ANN401
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, set | frozenset):
        return ", ".join(str(item) for item in sorted(value, key=str))
    if isinstance(value, list | tuple):
        return ", ".join(str(item) for item in value)
    return str(value)


def _markdown_cell(value: Any) -> str:  # noqa: ANN401
    return _text_cell(value).replace("|", r"\|").replace("\n", " ")


def _align_at(align: str, index: int) -> str:
    return align[index] if index < len(align) else "l"


def _pad(cell: str, width: int, align: str) -> str:
    return f"{cell:>{width}}" if align == "r" else f"{cell:<{width}}"


def _text_row(cells: Sequence[str], widths: Sequence[int], align: str) -> str:
    padded = [
        _pad(cell, width, _align_at(align, index))
        for index, (cell, width) in enumerate(zip(cells, widths, strict=True))
    ]
    return COLUMN_GAP.join(padded).rstrip()


def _text_table(table: Table) -> list[str]:
    rows = [[_text_cell(cell) for cell in row] for row in table.rows]
    heading = [f"{table.title} ({len(rows)})"] if table.title else []
    if not rows:
        return [*heading, table.empty]
    widths = [
        max(len(column), *(len(row[index]) for row in rows)) for index, column in enumerate(table.columns)
    ]
    rule = "-" * (sum(widths) + len(COLUMN_GAP) * (len(widths) - 1))
    return [
        *heading,
        _text_row(list(table.columns), widths, table.align),
        rule,
        *(_text_row(row, widths, table.align) for row in rows),
    ]


def _render_text(report: Report) -> str:
    lines = [report.title]
    lines.extend(f"{label}: {_text_cell(value)}" for label, value in report.summary)
    lines.extend(report.notes)
    for table in report.tables:
        lines.append("")
        lines.extend(_text_table(table))
    return "\n".join(lines)


def _markdown_table(table: Table) -> list[str]:
    header = "| " + " | ".join(table.columns) + " |"
    divider = (
        "| "
        + " | ".join(
            "---:" if _align_at(table.align, index) == "r" else "---" for index in range(len(table.columns))
        )
        + " |"
    )
    if not table.rows:
        return [f"_{table.empty}_"]
    return [
        header,
        divider,
        *("| " + " | ".join(_markdown_cell(cell) for cell in row) + " |" for row in table.rows),
    ]


def _render_markdown(report: Report) -> str:
    lines = [f"# {report.title}", ""]
    if report.summary:
        lines.extend(f"- **{label}:** {_markdown_cell(value)}" for label, value in report.summary)
        lines.append("")
    if report.notes:
        lines.extend(f"_{note}_" for note in report.notes)
        lines.append("")
    for table in report.tables:
        if table.title:
            lines.extend((f"## {table.title}", ""))
        lines.extend(_markdown_table(table))
        lines.append("")
    return "\n".join(lines).rstrip()


def _render_csv(report: Report) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    if not report.tables:
        writer.writerow(["Field", "Value"])
        writer.writerow(["Result", report.title])
        writer.writerows([label, _csv_cell(value)] for label, value in report.summary)
        return buf.getvalue()
    labelled = len(report.tables) > 1
    for index, table in enumerate(report.tables):
        if index:
            writer.writerow([])
        if labelled and table.title:
            writer.writerow([table.title])
        writer.writerow(list(table.columns))
        writer.writerows([_csv_cell(cell) for cell in row] for row in table.rows)
    return buf.getvalue()
