"""Output renderers for the jellyfin-watched report."""

from __future__ import annotations

import csv
import io
from typing import TYPE_CHECKING

import orjson

from jellyfin_utils.client import display_name, size_gb

if TYPE_CHECKING:
    from .models import Candidate

MEDIA_TYPE_ORDER = ["Movie", "Series", "Episode"]


def _format_item_line(c: Candidate, total_active_users: int) -> str:
    """Format a single candidate as a one-line summary with name, watch stats, and size."""
    name = display_name(c.item)
    size = f"{size_gb(c.item.size):>6.2f} GB"
    watch = f"{c.watch_count}/{total_active_users} users ({c.watch_percentage}%)"
    priority = "PRIORITY" if c.requester_watched else "standard"
    return f"{priority:<8} | {name:<50} | {watch} | {size}"


def _format_item_detail(c: Candidate) -> str:
    """Format the detail lines (watched-by, ID, path) shown below an item in verbose mode."""
    lines = [f"  Watched by: {', '.join(c.watched_by)}"]
    if c.requested_by:
        lines.append(f"  Requested by: {', '.join(c.requested_by)}")
    if c.watched_by_requester:
        lines.append(f"  Requester watched: {', '.join(c.watched_by_requester)}")
    lines.extend((f"  ID: {c.item.item_id}", f"  Path: {c.item.path}"))
    return "\n".join(lines)


def _summary_lines(
    candidates: list[Candidate],
    total_active_users: int,
    *,
    total_users: int,
    ignore_usernames: set[str],
    threshold: int,
    total_items: int,
    max_age_days: int | None,
    jellyseerr_enabled: bool,
) -> list[str]:
    """Build the header block shown above the candidate list."""
    lines = [f"Total users: {total_users}"]
    if ignore_usernames:
        lines.append(f"Ignoring users: {', '.join(sorted(ignore_usernames))}")
    lines.append(f"Active users analyzed: {total_active_users}")
    lines.append(f"Watch threshold: {threshold}% of users")
    lines.append(f"Total library items scanned: {total_items}")
    if max_age_days is not None:
        lines.append(f"(Plays older than {max_age_days} days are ignored)")
    if jellyseerr_enabled:
        lines.append("PRIORITY = requested in Jellyseerr and watched by its requester")
    lines.append(f"\nCandidate items (watched by >={threshold}% of users): {len(candidates)}")
    total_size = sum(size_gb(c.item.size) for c in candidates if not c.item.size_is_rollup)
    lines.append(f"Total size of candidates: {total_size:.2f} GB")
    if any(c.item.size_is_rollup for c in candidates):
        lines.append("(Series sizes total their episodes and are left out of the figure above.)")
    lines.append("")
    return lines


def render_text(
    candidates: list[Candidate],
    grouped: dict[str, list[Candidate]],
    total_active_users: int,
    *,
    quiet: bool,
    total_users: int,
    ignore_usernames: set[str],
    threshold: int,
    total_items: int,
    max_age_days: int | None,
    jellyseerr_enabled: bool,
) -> str:
    """Render the full text report as a string."""
    lines: list[str] = []

    if not quiet:
        lines.extend(
            _summary_lines(
                candidates,
                total_active_users,
                total_users=total_users,
                ignore_usernames=ignore_usernames,
                threshold=threshold,
                total_items=total_items,
                max_age_days=max_age_days,
                jellyseerr_enabled=jellyseerr_enabled,
            )
        )

    for media_type in MEDIA_TYPE_ORDER:
        items_of_type = grouped[media_type]
        if not items_of_type:
            continue

        if not quiet:
            lines.append(f"\n{'=' * 80}")
            lines.append(f"{media_type}s ({len(items_of_type)} items)")
            lines.append(f"{'=' * 80}")
            lines.append(
                "Priority | Title                                              | Watched        | Size"
            )

        for c in items_of_type:
            lines.append(_format_item_line(c, total_active_users))
            if not quiet:
                lines.append(_format_item_detail(c))

    return "\n".join(lines)


def render_json(
    candidates: list[Candidate],
    grouped: dict[str, list[Candidate]],
    *,
    base_url: str,
    total_users: int,
    ignore_usernames: set[str],
    total_active_users: int,
    threshold: int,
    total_items: int,
    max_age_days: int | None,
    jellyseerr_enabled: bool,
) -> bytes:
    """Render the structured JSON report as bytes."""
    payload = {
        "server": base_url,
        "total_users": total_users,
        "ignored_users": sorted(ignore_usernames),
        "active_users": total_active_users,
        "threshold_percent": threshold,
        "total_items": total_items,
        "max_age_days": max_age_days,
        "jellyseerr_requester_watch_prioritization": jellyseerr_enabled,
        "candidates_count": len(candidates),
        "candidates_by_type": {
            "movies": len(grouped["Movie"]),
            "series": len(grouped["Series"]),
            "episodes": len(grouped["Episode"]),
        },
        "candidates": [
            {
                "name": display_name(c.item),
                "type": c.item.item_type,
                "id": c.item.item_id,
                "path": c.item.path,
                "size": c.item.size,
                "watch_count": c.watch_count,
                "watch_percentage": c.watch_percentage,
                "watched_by": list(c.watched_by),
                "requested_by": list(c.requested_by),
                "watched_by_requester": list(c.watched_by_requester),
                "requester_watched": c.requester_watched,
            }
            for c in candidates
        ],
    }
    return orjson.dumps(payload, option=orjson.OPT_INDENT_2)


def render_markdown(candidates: list[Candidate], total_active_users: int) -> str:
    """Render candidates as a compact Markdown table."""
    lines = [
        "| Priority | Type | Title | Watched | Requested by | Requester watched | Size |",
        "| --- | --- | --- | --- | --- | --- | ---: |",
    ]
    for c in candidates:
        priority = "Priority" if c.requester_watched else "Standard"
        watch = f"{c.watch_count}/{total_active_users} ({c.watch_percentage}%)"
        lines.append(
            "| "
            + " | ".join(
                (
                    priority,
                    c.item.item_type,
                    display_name(c.item),
                    watch,
                    ", ".join(c.requested_by) or "—",
                    ", ".join(c.watched_by_requester) or "—",
                    f"{size_gb(c.item.size):.2f} GB",
                )
            )
            + " |"
        )
    return "\n".join(lines)


def render_csv(candidates: list[Candidate]) -> str:
    """Render the candidate list as CSV with headers."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
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
        ]
    )
    for c in candidates:
        writer.writerow(
            [
                c.item.item_type,
                display_name(c.item),
                c.watch_count,
                f"{c.watch_percentage}%",
                ", ".join(c.watched_by),
                ", ".join(c.watched_by_requester),
                ", ".join(c.requested_by),
                f"{size_gb(c.item.size):.2f}",
                c.item.item_id,
                c.item.path,
            ]
        )
    return buf.getvalue()
