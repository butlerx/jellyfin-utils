"""Library-analysis commands."""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from pathlib import Path

import click
import orjson

from jellyfin_utils.client import (
    LibraryItem,
    build_headers,
    display_name,
    get_all_items,
    get_users,
    get_watchers_per_item,
    size_gb,
)
from jellyfin_utils.jellyseerr import get_requesters_by_tmdb_id, get_requests
from jellyfin_utils.options import (
    connection_options,
    ignore_user_option,
    jellyseerr_options,
    output_option,
    quiet_option,
    require_jellyseerr_pair,
    threshold_option,
)
from jellyfin_utils.output import OutputFormat, Report, Table, emit
from jellyfin_utils.stale.cli import find_stale
from jellyfin_utils.watched.cli import find_candidates

from .render import render_csv, render_json, render_markdown, render_text


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
    require_jellyseerr_pair(jellyseerr_server, jellyseerr_token)

    headers = build_headers(token)
    users = get_users(base_url, headers)
    ignored = set(ignore_user)
    watchers = get_watchers_per_item(base_url, headers, users, ignored, max_age_days=None)
    active = sum(user.get("Name") not in ignored for user in users)
    items = get_all_items(base_url, headers)
    requesters_by_tmdb_id = (
        get_requesters_by_tmdb_id(jellyseerr_server, jellyseerr_token)
        if jellyseerr_server and jellyseerr_token
        else None
    )
    candidates = find_candidates(items, watchers, active, threshold, requesters_by_tmdb_id)
    stale = find_stale(items, watchers, active, 0, min_age, dt.datetime.now(dt.UTC), requesters_by_tmdb_id)
    merged: dict[str, dict] = {}
    for candidate in candidates:
        merged[candidate.item.item_id] = {
            "reason": "widely_watched",
            "item": display_name(candidate.item),
            "series": candidate.item.series_name,
            "id": candidate.item.item_id,
            "type": candidate.item.item_type,
            "path": candidate.item.path,
            "size_gib": round(size_gb(candidate.item.size), 2),
            "size_is_rollup": candidate.item.size_is_rollup,
            "watchers": candidate.watch_count,
            "requested_by": list(candidate.requested_by),
            "watched_by_requester": list(candidate.watched_by_requester),
            "requester_watched": candidate.requester_watched,
        }
    for item in stale:
        entry = merged.setdefault(
            item.item.item_id,
            {
                "reason": "stale",
                "item": display_name(item.item),
                "series": item.item.series_name,
                "id": item.item.item_id,
                "type": item.item.item_type,
                "path": item.item.path,
                "size_gib": round(size_gb(item.item.size), 2),
                "size_is_rollup": item.item.size_is_rollup,
                "watchers": item.watch_count,
                "requested_by": list(item.requested_by),
                "watched_by_requester": list(item.watched_by_requester),
                "requester_watched": item.requester_watched,
            },
        )
        if entry["reason"] == "widely_watched":
            entry["reason"] = "widely_watched_and_stale"
    results = sorted(
        merged.values(), key=lambda entry: (not entry["requester_watched"], -float(entry["size_gib"]))
    )
    jellyseerr_enabled = requesters_by_tmdb_id is not None

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
