"""Output renderers for the jellyfin-stale report."""

from __future__ import annotations

import csv
import io
from typing import TYPE_CHECKING

import orjson

from jellyfin_utils.client import display_name, size_gb

if TYPE_CHECKING:
    from .models import StaleItem

MEDIA_TYPE_ORDER = ["Movie", "Series", "Episode"]


def _format_item_line(s: StaleItem, total_active_users: int) -> str:
    name = display_name(s.item)
    size = f"{size_gb(s.item.size):>6.2f} GB"
    watch = f"{s.watch_count}/{total_active_users} users ({s.watch_percentage}%)"
    age = f"{s.age_days}d old" if s.age_days is not None else "age unknown"
    priority = "PRIORITY" if s.requester_watched else "standard"
    return f"{priority:<8} | {name:<50} | {watch} | {size} | {age}"


def _format_item_detail(s: StaleItem) -> str:
    lines = []
    if s.requested_by:
        lines.append(f"  Requested by: {', '.join(s.requested_by)}")
    if s.watched_by_requester:
        lines.append(f"  Requester watched: {', '.join(s.watched_by_requester)}")
    lines.extend((f"  ID: {s.item.item_id}", f"  Path: {s.item.path}"))
    return "\n".join(lines)


def render_text(
    stale_items: list[StaleItem],
    grouped: dict[str, list[StaleItem]],
    total_active_users: int,
    *,
    quiet: bool,
    total_users: int,
    ignore_usernames: set[str],
    max_watchers: int,
    total_items: int,
    min_age_days: int | None,
    jellyseerr_enabled: bool,
) -> str:
    """Render the full text report as a string."""
    lines: list[str] = []

    if not quiet:
        lines.append(f"Total users: {total_users}")
        if ignore_usernames:
            lines.append(f"Ignoring users: {', '.join(sorted(ignore_usernames))}")
        lines.append(f"Active users analyzed: {total_active_users}")
        lines.append(f"Stale threshold: watched by <= {max_watchers} users")
        lines.append(f"Total library items scanned: {total_items}")
        if min_age_days is not None:
            lines.append(f"Minimum age: {min_age_days} days (newer items excluded)")
        if jellyseerr_enabled:
            lines.append("PRIORITY = requested in Jellyseerr and watched by its requester")
        lines.append(f"\nStale items (watched by <={max_watchers} users): {len(stale_items)}")
        total_size = sum(size_gb(s.item.size) for s in stale_items)
        lines.append(f"Total size of stale content: {total_size:.2f} GB")
        lines.append("")

    for media_type in MEDIA_TYPE_ORDER:
        items_of_type = grouped[media_type]
        if not items_of_type:
            continue

        if not quiet:
            lines.append(f"\n{'=' * 80}")
            lines.append(f"{media_type}s ({len(items_of_type)} items)")
            lines.append(f"{'=' * 80}")
            lines.append(
                "Priority | Title                                              | Watched        | "
                "Size      | Age"
            )

        for s in items_of_type:
            lines.append(_format_item_line(s, total_active_users))
            if not quiet:
                lines.append(_format_item_detail(s))

    return "\n".join(lines)


def render_json(
    stale_items: list[StaleItem],
    grouped: dict[str, list[StaleItem]],
    *,
    base_url: str,
    total_users: int,
    ignore_usernames: set[str],
    total_active_users: int,
    max_watchers: int,
    total_items: int,
    min_age_days: int | None,
    jellyseerr_enabled: bool,
) -> bytes:
    """Render the structured JSON report as bytes."""
    payload = {
        "server": base_url,
        "total_users": total_users,
        "ignored_users": sorted(ignore_usernames),
        "active_users": total_active_users,
        "max_watchers": max_watchers,
        "min_age_days": min_age_days,
        "jellyseerr_requester_watch_prioritization": jellyseerr_enabled,
        "total_items": total_items,
        "stale_count": len(stale_items),
        "stale_by_type": {
            "movies": len(grouped["Movie"]),
            "series": len(grouped["Series"]),
            "episodes": len(grouped["Episode"]),
        },
        "stale_items": [
            {
                "name": display_name(s.item),
                "type": s.item.item_type,
                "id": s.item.item_id,
                "path": s.item.path,
                "size": s.item.size,
                "watch_count": s.watch_count,
                "watch_percentage": s.watch_percentage,
                "age_days": s.age_days,
                "requested_by": list(s.requested_by),
                "watched_by_requester": list(s.watched_by_requester),
                "requester_watched": s.requester_watched,
            }
            for s in stale_items
        ],
    }
    return orjson.dumps(payload, option=orjson.OPT_INDENT_2)


def render_markdown(stale_items: list[StaleItem], total_active_users: int) -> str:
    """Render stale items as a compact Markdown table."""
    lines = [
        "| Priority | Type | Title | Watched | Requested by | Requester watched | Age | Size |",
        "| --- | --- | --- | --- | --- | --- | --- | ---: |",
    ]
    for s in stale_items:
        priority = "Priority" if s.requester_watched else "Standard"
        watch = f"{s.watch_count}/{total_active_users} ({s.watch_percentage}%)"
        age = f"{s.age_days}d" if s.age_days is not None else "—"
        lines.append(
            "| "
            + " | ".join(
                (
                    priority,
                    s.item.item_type,
                    display_name(s.item),
                    watch,
                    ", ".join(s.requested_by) or "—",
                    ", ".join(s.watched_by_requester) or "—",
                    age,
                    f"{size_gb(s.item.size):.2f} GB",
                )
            )
            + " |"
        )
    return "\n".join(lines)


def render_csv(stale_items: list[StaleItem]) -> str:
    """Render the stale item list as CSV with headers."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
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
        ]
    )
    for s in stale_items:
        writer.writerow(
            [
                s.item.item_type,
                display_name(s.item),
                s.watch_count,
                f"{s.watch_percentage}%",
                ", ".join(s.watched_by_requester),
                ", ".join(s.requested_by),
                s.age_days if s.age_days is not None else "",
                f"{size_gb(s.item.size):.2f}",
                s.item.item_id,
                s.item.path,
            ]
        )
    return buf.getvalue()
