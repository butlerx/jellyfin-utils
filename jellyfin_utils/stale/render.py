"""Output renderers for the jellyfin-stale report."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import orjson

from jellyfin_utils.client import display_name, size_gb
from jellyfin_utils.media.render import (
    MEDIA_TYPE_ORDER,
    REQUESTER_PRIORITY_LEGEND,
    ROLLUP_NOTE,
    MediaRenderContext,
    common_summary_lines,
    contains_rollup,
    emit_media_output,
    format_watch_count,
    group_by_media_type,
    media_type_counts,
    render_csv_rows,
    total_non_rollup_size,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from jellyfin_utils.media import MediaAnalysisContext
    from jellyfin_utils.output import OutputFormat

    from .models import StaleItem


@dataclass(frozen=True)
class StaleRenderData:
    """All immutable inputs required to render a stale report."""

    stale_items: tuple[StaleItem, ...]
    grouped: Mapping[str, tuple[StaleItem, ...]]
    context: MediaRenderContext
    max_watchers: int
    min_age_days: int | None
    quiet: bool


def build_render_data(
    stale_items: Sequence[StaleItem],
    analysis: MediaAnalysisContext,
    *,
    max_watchers: int,
    min_age_days: int | None,
    quiet: bool,
) -> StaleRenderData:
    """Build immutable stale-report data from analysis results."""
    frozen_items = tuple(stale_items)
    return StaleRenderData(
        stale_items=frozen_items,
        grouped=group_by_media_type(frozen_items, lambda stale_item: stale_item.item.item_type),
        context=MediaRenderContext.from_analysis(analysis),
        max_watchers=max_watchers,
        min_age_days=min_age_days,
        quiet=quiet,
    )


def render(data: StaleRenderData, output_format: OutputFormat) -> None:
    """Render and emit stale output in the selected format."""
    emit_media_output(
        output_format,
        text=lambda: render_text(data),
        json=lambda: render_json(data),
        csv_output=lambda: render_csv(data),
        markdown=lambda: render_markdown(data),
    )


def _format_item_line(stale_item: StaleItem, total_active_users: int) -> str:
    name = display_name(stale_item.item)
    size = f"{size_gb(stale_item.item.size):>6.2f} GB"
    watch = format_watch_count(
        stale_item.watch_count,
        total_active_users,
        stale_item.watch_percentage,
    )
    age = f"{stale_item.age_days}d old" if stale_item.age_days is not None else "age unknown"
    priority = "PRIORITY" if stale_item.requester_watched else "standard"
    return f"{priority:<8} | {name:<50} | {watch} | {size} | {age}"


def _format_item_detail(stale_item: StaleItem) -> str:
    lines = []
    if stale_item.requested_by:
        lines.append(f"  Requested by: {', '.join(stale_item.requested_by)}")
    if stale_item.watched_by_requester:
        lines.append(f"  Requester watched: {', '.join(stale_item.watched_by_requester)}")
    lines.extend((f"  ID: {stale_item.item.item_id}", f"  Path: {stale_item.item.path}"))
    return "\n".join(lines)


def _summary_lines(data: StaleRenderData) -> list[str]:
    """Build the header block shown above the stale list."""
    lines = common_summary_lines(data.context)
    lines.append(f"Stale threshold: watched by <= {data.max_watchers} users")
    lines.append(f"Total library items scanned: {data.context.total_items}")
    if data.min_age_days is not None:
        lines.append(f"Minimum age: {data.min_age_days} days (newer items excluded)")
    if data.context.jellyseerr_enabled:
        lines.append(REQUESTER_PRIORITY_LEGEND)
    lines.append(f"\nStale items (watched by <={data.max_watchers} users): {len(data.stale_items)}")
    total_size = total_non_rollup_size(data.stale_items, lambda stale_item: stale_item.item)
    lines.append(f"Total size of stale content: {total_size:.2f} GB")
    if contains_rollup(data.stale_items, lambda stale_item: stale_item.item):
        lines.append(ROLLUP_NOTE)
    lines.append("")
    return lines


def render_text(data: StaleRenderData) -> str:
    """Render the full text report as a string."""
    lines: list[str] = []

    if not data.quiet:
        lines.extend(_summary_lines(data))

    for media_type in MEDIA_TYPE_ORDER:
        items_of_type = data.grouped[media_type]
        if not items_of_type:
            continue

        if not data.quiet:
            lines.append(f"\n{'=' * 80}")
            lines.append(f"{media_type}s ({len(items_of_type)} items)")
            lines.append(f"{'=' * 80}")
            lines.append(
                "Priority | Title                                              | Watched        | "
                "Size      | Age"
            )

        for stale_item in items_of_type:
            lines.append(_format_item_line(stale_item, data.context.total_active_users))
            if not data.quiet:
                lines.append(_format_item_detail(stale_item))

    return "\n".join(lines)


def render_json(data: StaleRenderData) -> bytes:
    """Render the structured JSON report as bytes."""
    payload = {
        "server": data.context.base_url,
        "total_users": data.context.total_users,
        "ignored_users": sorted(data.context.ignored_usernames),
        "active_users": data.context.total_active_users,
        "max_watchers": data.max_watchers,
        "min_age_days": data.min_age_days,
        "jellyseerr_requester_watch_prioritization": data.context.jellyseerr_enabled,
        "total_items": data.context.total_items,
        "stale_count": len(data.stale_items),
        "stale_by_type": media_type_counts(data.grouped),
        "stale_items": [
            {
                "name": display_name(stale_item.item),
                "type": stale_item.item.item_type,
                "id": stale_item.item.item_id,
                "path": stale_item.item.path,
                "size": stale_item.item.size,
                "watch_count": stale_item.watch_count,
                "watch_percentage": stale_item.watch_percentage,
                "age_days": stale_item.age_days,
                "requested_by": list(stale_item.requested_by),
                "watched_by_requester": list(stale_item.watched_by_requester),
                "requester_watched": stale_item.requester_watched,
            }
            for stale_item in data.stale_items
        ],
    }
    return orjson.dumps(payload, option=orjson.OPT_INDENT_2)


def render_markdown(data: StaleRenderData) -> str:
    """Render stale items as a compact Markdown table."""
    lines = [
        "| Priority | Type | Title | Watched | Requested by | Requester watched | Age | Size |",
        "| --- | --- | --- | --- | --- | --- | --- | ---: |",
    ]
    for stale_item in data.stale_items:
        priority = "Priority" if stale_item.requester_watched else "Standard"
        watch = f"{stale_item.watch_count}/{data.context.total_active_users} ({stale_item.watch_percentage}%)"
        age = f"{stale_item.age_days}d" if stale_item.age_days is not None else "—"
        lines.append(
            "| "
            + " | ".join(
                (
                    priority,
                    stale_item.item.item_type,
                    display_name(stale_item.item),
                    watch,
                    ", ".join(stale_item.requested_by) or "—",
                    ", ".join(stale_item.watched_by_requester) or "—",
                    age,
                    f"{size_gb(stale_item.item.size):.2f} GB",
                )
            )
            + " |"
        )
    return "\n".join(lines)


def render_csv(data: StaleRenderData) -> str:
    """Render the stale item list as CSV with headers."""
    return render_csv_rows(
        (
            "Type",
            "Name",
            "Watch Count",
            "Watch %",
            "Requester Watched",
            "Requested By",
            "Age (days)",
            "File Size (GB)",
            "ID",
            "Path",
        ),
        (
            (
                stale_item.item.item_type,
                display_name(stale_item.item),
                stale_item.watch_count,
                f"{stale_item.watch_percentage}%",
                ", ".join(stale_item.watched_by_requester),
                ", ".join(stale_item.requested_by),
                stale_item.age_days if stale_item.age_days is not None else "",
                f"{size_gb(stale_item.item.size):.2f}",
                stale_item.item.item_id,
                stale_item.item.path,
            )
            for stale_item in data.stale_items
        ),
    )
