"""Library-analysis commands."""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

import click
import orjson

from jellyfin_utils.client.library import get_all_items
from jellyfin_utils.client.models import LibraryItem, display_name, size_gb
from jellyfin_utils.client.transport import build_headers, get_users
from jellyfin_utils.client.watch import get_watchers_per_item
from jellyfin_utils.jellyseerr import get_requesters_by_tmdb_id, get_requests
from jellyfin_utils.media import load_media_analysis
from jellyfin_utils.options import (
    connection_options,
    ignore_user_option,
    jellyseerr_options,
    output_option,
    quiet_option,
    threshold_option,
)
from jellyfin_utils.output import OutputFormat, Report, Table, emit
from jellyfin_utils.stale.service import find_stale
from jellyfin_utils.watched.service import find_candidates

from .render import ReclaimRow, render_csv, render_json, render_markdown, render_text

if TYPE_CHECKING:
    from jellyfin_utils.stale.models import StaleItem
    from jellyfin_utils.watched.models import Candidate


def _reclaim_row(
    item: LibraryItem,
    *,
    reason: str,
    watchers: int,
    requested_by: tuple[str, ...],
    watched_by_requester: tuple[str, ...],
    requester_watched: bool,
) -> ReclaimRow:
    """Convert one analyzed item to the stable reclaim rendering shape."""
    return {
        "reason": reason,
        "item": display_name(item),
        "series": item.series_name,
        "id": item.item_id,
        "type": item.item_type,
        "path": item.path,
        "size_gib": round(size_gb(item.size), 2),
        "size_is_rollup": item.size_is_rollup,
        "watchers": watchers,
        "requested_by": list(requested_by),
        "watched_by_requester": list(watched_by_requester),
        "requester_watched": requester_watched,
    }


def _candidate_reclaim_row(candidate: Candidate) -> ReclaimRow:
    return _reclaim_row(
        candidate.item,
        reason="widely_watched",
        watchers=candidate.watch_count,
        requested_by=candidate.requested_by,
        watched_by_requester=candidate.watched_by_requester,
        requester_watched=candidate.requester_watched,
    )


def _stale_reclaim_row(stale_item: StaleItem) -> ReclaimRow:
    return _reclaim_row(
        stale_item.item,
        reason="stale",
        watchers=stale_item.watch_count,
        requested_by=stale_item.requested_by,
        watched_by_requester=stale_item.watched_by_requester,
        requester_watched=stale_item.requester_watched,
    )


@click.command("reclaim")
@connection_options
@jellyseerr_options
@ignore_user_option
@threshold_option
@click.option("--min-age", type=int, default=90, show_default=True, help="Minimum stale-item age in days.")
@output_option
@quiet_option
def reclaim(
    base_url: str,
    token: str,
    ignore_user: tuple[str, ...],
    threshold: int,
    min_age: int,
    jellyseerr_server: str | None,
    jellyseerr_token: str | None,
    output_format: OutputFormat,
    quiet: bool,
) -> None:
    """Rank watched and stale content for cleanup review."""
    context = load_media_analysis(
        base_url,
        token,
        ignore_user,
        max_watch_age_days=None,
        jellyseerr_server=jellyseerr_server,
        jellyseerr_token=jellyseerr_token,
        build_headers_fn=build_headers,
        get_users_fn=get_users,
        get_watchers_fn=get_watchers_per_item,
        get_items_fn=get_all_items,
        get_requesters_fn=get_requesters_by_tmdb_id,
    )
    candidates = find_candidates(
        context.items,
        context.watchers,
        context.active_user_count,
        threshold,
        context.requesters_by_tmdb_id,
    )
    stale = find_stale(
        context.items,
        context.watchers,
        context.active_user_count,
        0,
        min_age,
        dt.datetime.now(dt.UTC),
        context.requesters_by_tmdb_id,
    )
    merged = {candidate.item.item_id: _candidate_reclaim_row(candidate) for candidate in candidates}
    for stale_item in stale:
        entry = merged.setdefault(stale_item.item.item_id, _stale_reclaim_row(stale_item))
        if entry["reason"] == "widely_watched":
            entry["reason"] = "widely_watched_and_stale"
    results = sorted(merged.values(), key=lambda entry: (not entry["requester_watched"], -entry["size_gib"]))
    jellyseerr_enabled = context.jellyseerr_enabled

    match output_format:
        case OutputFormat.JSON:
            click.echo(render_json(results, jellyseerr_enabled=jellyseerr_enabled))
        case OutputFormat.CSV:
            click.echo(render_csv(results), nl=False)
        case OutputFormat.MARKDOWN:
            click.echo(render_markdown(results))
        case _:
            click.echo(render_text(results, jellyseerr_enabled=jellyseerr_enabled, quiet=quiet))


@click.command("duplicates")
@connection_options
@output_option
def duplicates(base_url: str, token: str, output_format: OutputFormat) -> None:
    """Find on-disk items with the same TMDb identifier."""
    items = get_all_items(base_url, build_headers(token))
    grouped: dict[tuple[str, int], list[LibraryItem]] = defaultdict(list)
    for item in items:
        if item.path and item.tmdb_id is not None:
            grouped[(item.item_type, item.tmdb_id)].append(item)
    duplicate_groups = [(key, group) for key, group in grouped.items() if len(group) > 1]
    emit(
        Report(
            title="Duplicate items",
            payload={
                "duplicate_groups": [
                    {
                        "type": kind,
                        "tmdb_id": tmdb_id,
                        "items": [
                            {
                                "id": item.item_id,
                                "name": item.name,
                                "year": item.production_year,
                                "path": item.path,
                                "size_gib": round(size_gb(item.size), 2),
                            }
                            for item in group
                        ],
                    }
                    for (kind, tmdb_id), group in duplicate_groups
                ],
                "count": len(duplicate_groups),
            },
            summary=(
                ("Items scanned", len(items)),
                ("Duplicate groups", len(duplicate_groups)),
                ("Copies on disk", sum(len(group) for _, group in duplicate_groups)),
            ),
            tables=(
                Table(
                    columns=("Type", "TMDb ID", "Title", "Year", "Size (GiB)", "ID", "Path"),
                    align="lrlrrll",
                    rows=[
                        (
                            kind,
                            tmdb_id,
                            item.name,
                            item.production_year,
                            f"{size_gb(item.size):.2f}",
                            item.item_id,
                            item.path,
                        )
                        for (kind, tmdb_id), group in duplicate_groups
                        for item in group
                    ],
                    empty="No duplicates found.",
                ),
            ),
        ),
        output_format,
    )


@click.command("health")
@connection_options
@output_option
def health(base_url: str, token: str, output_format: OutputFormat) -> None:
    """Report library records that cannot be used for storage analysis."""
    items = get_all_items(base_url, build_headers(token))
    missing_path = [item for item in items if not item.path]
    no_size = [item for item in items if item.path and item.size == 0]
    emit(
        Report(
            title="Library health",
            payload={
                "items_scanned": len(items),
                "missing_path": [
                    {"id": item.item_id, "name": item.name, "type": item.item_type} for item in missing_path
                ],
                "zero_size": [{"id": item.item_id, "name": item.name, "path": item.path} for item in no_size],
            },
            summary=(
                ("Items scanned", len(items)),
                ("Missing path", len(missing_path)),
                ("Zero size", len(no_size)),
            ),
            tables=(
                Table(
                    title="Items with no on-disk path",
                    columns=("Type", "Title", "ID"),
                    rows=[(item.item_type, item.name, item.item_id) for item in missing_path],
                    empty="Every item has a path.",
                ),
                Table(
                    title="On-disk items reporting zero bytes",
                    columns=("Title", "ID", "Path"),
                    rows=[(item.name, item.item_id, item.path) for item in no_size],
                    empty="Every on-disk item reports a size.",
                ),
            ),
        ),
        output_format,
    )


@click.command("requests")
@connection_options
@jellyseerr_options(required=True)
@output_option
def requests(
    base_url: str,
    token: str,
    jellyseerr_server: str,
    jellyseerr_token: str,
    output_format: OutputFormat,
) -> None:
    """Reconcile Jellyseerr requests with media currently in Jellyfin."""
    items = get_all_items(base_url, build_headers(token))
    available = {item.tmdb_id for item in items if item.tmdb_id is not None and item.path}
    results = [
        {**request, "available_in_jellyfin": request.get("tmdb_id") in available}
        for request in get_requests(jellyseerr_server, jellyseerr_token)
    ]
    landed = sum(bool(request["available_in_jellyfin"]) for request in results)
    emit(
        Report(
            title="Jellyseerr requests vs. library",
            payload={"requests": results, "count": len(results)},
            summary=(
                ("Requests", len(results)),
                ("Available in Jellyfin", landed),
                ("Missing from Jellyfin", len(results) - landed),
            ),
            tables=(
                Table(
                    columns=("ID", "Status", "Requested by", "TMDb ID", "Media type", "In Jellyfin"),
                    align="rrlrll",
                    rows=[
                        (
                            request["id"],
                            request["status"],
                            request["requested_by"],
                            request["tmdb_id"],
                            request["media_type"],
                            request["available_in_jellyfin"],
                        )
                        for request in results
                    ],
                    empty="No requests found.",
                ),
            ),
            notes=("Status is Jellyseerr's own request-status code, passed through unchanged.",),
        ),
        output_format,
    )


@click.command("report")
@connection_options
@click.option("--snapshot", type=click.Path(path_type=Path), help="JSON file to write with this report.")
@output_option
def report(base_url: str, token: str, snapshot: Path | None, output_format: OutputFormat) -> None:
    """Summarize library size and item counts, optionally saving a snapshot."""
    items = get_all_items(base_url, build_headers(token))
    by_type: dict[str, dict[str, int]] = defaultdict(lambda: {"items": 0, "bytes": 0})
    for item in items:
        by_type[item.item_type]["items"] += 1
        by_type[item.item_type]["bytes"] += item.size
    payload = {
        "items": len(items),
        # Series sizes total their episodes, which are counted individually.
        "total_gib": round(sum(item.size for item in items if not item.size_is_rollup) / 1024**3, 2),
        "by_type": by_type,
    }
    if snapshot is not None:
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(orjson.dumps(payload, option=orjson.OPT_INDENT_2))
    emit(
        Report(
            title="Library report",
            payload=payload,
            summary=(("Items", payload["items"]), ("Total GiB", f"{payload['total_gib']:.2f}")),
            tables=(
                Table(
                    title="By media type",
                    columns=("Type", "Items", "Bytes", "Size (GiB)"),
                    align="lrrr",
                    rows=[
                        (kind, counts["items"], counts["bytes"], f"{counts['bytes'] / 1024**3:.2f}")
                        for kind, counts in sorted(by_type.items())
                    ],
                    empty="Library is empty.",
                ),
            ),
            notes=("Series repeat the bytes already counted under Episode; Total GiB counts them once.",),
        ),
        output_format,
    )
