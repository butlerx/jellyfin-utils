"""Shared rendering mechanics for watched and stale media reports."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

import click

from jellyfin_utils.client import size_gb
from jellyfin_utils.output import OutputFormat

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from jellyfin_utils.client import LibraryItem

    from .context import MediaAnalysisContext

MEDIA_TYPE_ORDER = ("Movie", "Series", "Episode")
REQUESTER_PRIORITY_LEGEND = "PRIORITY = requested in Jellyseerr and watched by its requester"
ROLLUP_NOTE = "(Series sizes total their episodes and are left out of the figure above.)"


@dataclass(frozen=True)
class MediaRenderContext:
    """Immutable report metadata shared by watched and stale output."""

    base_url: str
    total_users: int
    ignored_usernames: frozenset[str]
    total_active_users: int
    total_items: int
    jellyseerr_enabled: bool

    @classmethod
    def from_analysis(cls, context: MediaAnalysisContext) -> MediaRenderContext:
        """Build rendering metadata from loaded analysis data."""
        return cls(
            base_url=context.base_url,
            total_users=context.total_users,
            ignored_usernames=context.ignored_usernames,
            total_active_users=context.active_user_count,
            total_items=context.total_items,
            jellyseerr_enabled=context.jellyseerr_enabled,
        )


def group_by_media_type[T](
    entries: Sequence[T],
    get_media_type: Callable[[T], str],
) -> Mapping[str, tuple[T, ...]]:
    """Group entries by the report's fixed media-type order."""
    grouped: dict[str, list[T]] = {media_type: [] for media_type in MEDIA_TYPE_ORDER}
    for entry in entries:
        media_type = get_media_type(entry)
        if media_type in grouped:
            grouped[media_type].append(entry)
    return MappingProxyType({media_type: tuple(items) for media_type, items in grouped.items()})


def common_summary_lines(context: MediaRenderContext) -> list[str]:
    """Build the user-related summary lines shared by media reports."""
    lines = [f"Total users: {context.total_users}"]
    if context.ignored_usernames:
        lines.append(f"Ignoring users: {', '.join(sorted(context.ignored_usernames))}")
    lines.append(f"Active users analyzed: {context.total_active_users}")
    return lines


def media_type_counts(grouped: Mapping[str, Sequence[object]]) -> dict[str, int]:
    """Return JSON-ready movie, series, and episode counts."""
    return {
        "movies": len(grouped["Movie"]),
        "series": len(grouped["Series"]),
        "episodes": len(grouped["Episode"]),
    }


def format_watch_count(count: int, total_active_users: int, percentage: float) -> str:
    """Format the watch ratio used in text report rows."""
    return f"{count}/{total_active_users} users ({percentage}%)"


def total_non_rollup_size[T](entries: Iterable[T], get_item: Callable[[T], LibraryItem]) -> float:
    """Return total size in GiB without double-counting rolled-up series."""
    return sum(size_gb(item.size) for entry in entries if not (item := get_item(entry)).size_is_rollup)


def contains_rollup[T](entries: Iterable[T], get_item: Callable[[T], LibraryItem]) -> bool:
    """Whether a report contains at least one rolled-up series size."""
    return any(get_item(entry).size_is_rollup for entry in entries)


def render_csv_rows(headers: Sequence[object], rows: Iterable[Sequence[object]]) -> str:
    """Render rows using the CSV dialect shared by both media reports."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    writer.writerows(rows)
    return buffer.getvalue()


def emit_media_output(
    output_format: OutputFormat,
    *,
    text: Callable[[], str],
    json: Callable[[], bytes],
    csv_output: Callable[[], str],
    markdown: Callable[[], str],
) -> None:
    """Render and emit one media report in the selected output format."""
    match output_format:
        case OutputFormat.JSON:
            click.echo(json())
        case OutputFormat.CSV:
            click.echo(csv_output(), nl=False)
        case OutputFormat.MARKDOWN:
            click.echo(markdown())
        case _:
            click.echo(text())
