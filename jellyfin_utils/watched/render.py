"""Output renderers for the jellyfin-watched report."""

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

    from .models import Candidate


@dataclass(frozen=True)
class WatchedRenderData:
    """All immutable inputs required to render a watched report."""

    candidates: tuple[Candidate, ...]
    grouped: Mapping[str, tuple[Candidate, ...]]
    context: MediaRenderContext
    threshold: int
    max_age_days: int | None
    quiet: bool


def build_render_data(
    candidates: Sequence[Candidate],
    analysis: MediaAnalysisContext,
    *,
    threshold: int,
    max_age_days: int | None,
    quiet: bool,
) -> WatchedRenderData:
    """Build immutable watched-report data from analysis results."""
    frozen_candidates = tuple(candidates)
    return WatchedRenderData(
        candidates=frozen_candidates,
        grouped=group_by_media_type(frozen_candidates, lambda candidate: candidate.item.item_type),
        context=MediaRenderContext.from_analysis(analysis),
        threshold=threshold,
        max_age_days=max_age_days,
        quiet=quiet,
    )


def render(data: WatchedRenderData, output_format: OutputFormat) -> None:
    """Render and emit watched output in the selected format."""
    emit_media_output(
        output_format,
        text=lambda: render_text(data),
        json=lambda: render_json(data),
        csv_output=lambda: render_csv(data),
        markdown=lambda: render_markdown(data),
    )


def _format_item_line(candidate: Candidate, total_active_users: int) -> str:
    """Format a single candidate as a one-line summary with name, watch stats, and size."""
    name = display_name(candidate.item)
    size = f"{size_gb(candidate.item.size):>6.2f} GB"
    watch = format_watch_count(
        candidate.watch_count,
        total_active_users,
        candidate.watch_percentage,
    )
    priority = "PRIORITY" if candidate.requester_watched else "standard"
    return f"{priority:<8} | {name:<50} | {watch} | {size}"


def _format_item_detail(candidate: Candidate) -> str:
    """Format the detail lines shown below a watched item in verbose mode."""
    lines = [f"  Watched by: {', '.join(candidate.watched_by)}"]
    if candidate.requested_by:
        lines.append(f"  Requested by: {', '.join(candidate.requested_by)}")
    if candidate.watched_by_requester:
        lines.append(f"  Requester watched: {', '.join(candidate.watched_by_requester)}")
    lines.extend((f"  ID: {candidate.item.item_id}", f"  Path: {candidate.item.path}"))
    return "\n".join(lines)


def _summary_lines(data: WatchedRenderData) -> list[str]:
    """Build the header block shown above the candidate list."""
    lines = common_summary_lines(data.context)
    lines.append(f"Watch threshold: {data.threshold}% of users")
    lines.append(f"Total library items scanned: {data.context.total_items}")
    if data.max_age_days is not None:
        lines.append(f"(Plays older than {data.max_age_days} days are ignored)")
    if data.context.jellyseerr_enabled:
        lines.append(REQUESTER_PRIORITY_LEGEND)
    lines.append(f"\nCandidate items (watched by >={data.threshold}% of users): {len(data.candidates)}")
    total_size = total_non_rollup_size(data.candidates, lambda candidate: candidate.item)
    lines.append(f"Total size of candidates: {total_size:.2f} GB")
    if contains_rollup(data.candidates, lambda candidate: candidate.item):
        lines.append(ROLLUP_NOTE)
    lines.append("")
    return lines


def render_text(data: WatchedRenderData) -> str:
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
                "Priority | Title                                              | Watched        | Size"
            )

        for candidate in items_of_type:
            lines.append(_format_item_line(candidate, data.context.total_active_users))
            if not data.quiet:
                lines.append(_format_item_detail(candidate))

    return "\n".join(lines)


def render_json(data: WatchedRenderData) -> bytes:
    """Render the structured JSON report as bytes."""
    payload = {
        "server": data.context.base_url,
        "total_users": data.context.total_users,
        "ignored_users": sorted(data.context.ignored_usernames),
        "active_users": data.context.total_active_users,
        "threshold_percent": data.threshold,
        "total_items": data.context.total_items,
        "max_age_days": data.max_age_days,
        "jellyseerr_requester_watch_prioritization": data.context.jellyseerr_enabled,
        "candidates_count": len(data.candidates),
        "candidates_by_type": media_type_counts(data.grouped),
        "candidates": [
            {
                "name": display_name(candidate.item),
                "type": candidate.item.item_type,
                "id": candidate.item.item_id,
                "path": candidate.item.path,
                "size": candidate.item.size,
                "watch_count": candidate.watch_count,
                "watch_percentage": candidate.watch_percentage,
                "watched_by": list(candidate.watched_by),
                "requested_by": list(candidate.requested_by),
                "watched_by_requester": list(candidate.watched_by_requester),
                "requester_watched": candidate.requester_watched,
            }
            for candidate in data.candidates
        ],
    }
    return orjson.dumps(payload, option=orjson.OPT_INDENT_2)


def render_markdown(data: WatchedRenderData) -> str:
    """Render candidates as a compact Markdown table."""
    lines = [
        "| Priority | Type | Title | Watched | Requested by | Requester watched | Size |",
        "| --- | --- | --- | --- | --- | --- | ---: |",
    ]
    for candidate in data.candidates:
        priority = "Priority" if candidate.requester_watched else "Standard"
        watch = f"{candidate.watch_count}/{data.context.total_active_users} ({candidate.watch_percentage}%)"
        lines.append(
            "| "
            + " | ".join(
                (
                    priority,
                    candidate.item.item_type,
                    display_name(candidate.item),
                    watch,
                    ", ".join(candidate.requested_by) or "—",
                    ", ".join(candidate.watched_by_requester) or "—",
                    f"{size_gb(candidate.item.size):.2f} GB",
                )
            )
            + " |"
        )
    return "\n".join(lines)


def render_csv(data: WatchedRenderData) -> str:
    """Render the candidate list as CSV with headers."""
    return render_csv_rows(
        (
            "Type",
            "Name",
            "Watch Count",
            "Watch %",
            "Watched By",
            "Requester Watched",
            "Requested By",
            "File Size (GB)",
            "ID",
            "Path",
        ),
        (
            (
                candidate.item.item_type,
                display_name(candidate.item),
                candidate.watch_count,
                f"{candidate.watch_percentage}%",
                ", ".join(candidate.watched_by),
                ", ".join(candidate.watched_by_requester),
                ", ".join(candidate.requested_by),
                f"{size_gb(candidate.item.size):.2f}",
                candidate.item.item_id,
                candidate.item.path,
            )
            for candidate in data.candidates
        ),
    )
