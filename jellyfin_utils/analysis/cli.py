"""Library-analysis commands."""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from pathlib import Path
from typing import Any

import click
import orjson

from jellyfin_utils.client import build_headers, get_all_items, get_users, get_watchers_per_item, size_gb
from jellyfin_utils.jellyseerr import get_requests
from jellyfin_utils.stale.cli import find_stale
from jellyfin_utils.watched.cli import find_candidates


def _json(value: object) -> None:
    click.echo(orjson.dumps(value, option=orjson.OPT_INDENT_2).decode())


def _connection_options(command: Any) -> Any:  # noqa: ANN401
    command = click.option("--token", envvar="JELLYFIN_TOKEN", required=True, help="Jellyfin API key.")(
        command
    )
    return click.option("--server", envvar="JELLYFIN_SERVER", required=True, help="Jellyfin server URL.")(
        command
    )


@click.command()
@_connection_options
@click.option("--ignore-user", multiple=True, help="Username to exclude (repeatable).")
@click.option("--threshold", default=80, show_default=True, help="Watched percentage required.")
@click.option("--min-age", default=90, show_default=True, help="Minimum stale-item age in days.")
def reclaim(server: str, token: str, ignore_user: tuple[str, ...], threshold: int, min_age: int) -> None:
    """Rank watched and stale content for cleanup review."""
    headers = build_headers(token)
    base_url = server.rstrip("/")
    users = get_users(base_url, headers)
    ignored = set(ignore_user)
    watchers = get_watchers_per_item(base_url, headers, users, ignored, max_age_days=None)
    active = sum(user.get("Name") not in ignored for user in users)
    items = get_all_items(base_url, headers)
    candidates = find_candidates(items, watchers, active, threshold)
    stale = find_stale(items, watchers, active, 0, min_age, dt.datetime.now(dt.UTC))
    merged: dict[str, dict] = {}
    for candidate in candidates:
        merged[candidate.item.item_id] = {
            "reason": "widely_watched",
            "item": candidate.item.name,
            "id": candidate.item.item_id,
            "size_gib": round(size_gb(candidate.item.size), 2),
            "watchers": candidate.watch_count,
        }
    for item in stale:
        entry = merged.setdefault(
            item.item.item_id,
            {
                "reason": "stale",
                "item": item.item.name,
                "id": item.item.item_id,
                "size_gib": round(size_gb(item.item.size), 2),
                "watchers": item.watch_count,
            },
        )
        if entry["reason"] == "widely_watched":
            entry["reason"] = "widely_watched_and_stale"
    results = sorted(merged.values(), key=lambda entry: -float(entry["size_gib"]))
    _json(
        {
            "candidates": results,
            "count": len(results),
            "estimated_reclaimable_gib": round(sum(float(entry["size_gib"]) for entry in results), 2),
        }
    )


@click.command()
@_connection_options
def duplicates(server: str, token: str) -> None:
    """Find on-disk items with the same TMDb identifier."""
    items = get_all_items(server.rstrip("/"), build_headers(token))
    grouped: dict[tuple[str, int], list] = defaultdict(list)
    for item in items:
        if item.path and item.tmdb_id is not None:
            grouped[(item.item_type, item.tmdb_id)].append(item)
    groups = [
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
        for (kind, tmdb_id), group in grouped.items()
        if len(group) > 1
    ]
    _json({"duplicate_groups": groups, "count": len(groups)})


@click.command()
@_connection_options
def health(server: str, token: str) -> None:
    """Report library records that cannot be used for storage analysis."""
    items = get_all_items(server.rstrip("/"), build_headers(token))
    missing_path = [item for item in items if not item.path]
    no_size = [item for item in items if item.path and item.size == 0]
    _json(
        {
            "items_scanned": len(items),
            "missing_path": [
                {"id": item.item_id, "name": item.name, "type": item.item_type} for item in missing_path
            ],
            "zero_size": [{"id": item.item_id, "name": item.name, "path": item.path} for item in no_size],
        }
    )


@click.command("requests")
@_connection_options
@click.option("--jellyseerr-server", envvar="JELLYSEERR_SERVER", required=True, help="Jellyseerr server URL.")
@click.option("--jellyseerr-token", envvar="JELLYSEERR_TOKEN", required=True, help="Jellyseerr API key.")
def requests(server: str, token: str, jellyseerr_server: str, jellyseerr_token: str) -> None:
    """Reconcile Jellyseerr requests with media currently in Jellyfin."""
    items = get_all_items(server.rstrip("/"), build_headers(token))
    available = {item.tmdb_id for item in items if item.tmdb_id is not None and item.path}
    results = get_requests(jellyseerr_server.rstrip("/"), jellyseerr_token)
    _json(
        {
            "requests": [
                {**request, "available_in_jellyfin": request.get("tmdb_id") in available}
                for request in results
            ],
            "count": len(results),
        }
    )


@click.command()
@_connection_options
@click.option(
    "--snapshot", type=click.Path(path_type=Path), help="Optional JSON file to write with this report."
)
def report(server: str, token: str, snapshot: Path | None) -> None:
    """Summarize library size and item counts, optionally saving a snapshot."""
    items = get_all_items(server.rstrip("/"), build_headers(token))
    by_type: dict[str, dict[str, int]] = defaultdict(lambda: {"items": 0, "bytes": 0})
    for item in items:
        by_type[item.item_type]["items"] += 1
        by_type[item.item_type]["bytes"] += item.size
    payload = {
        "items": len(items),
        "total_gib": round(sum(item.size for item in items) / 1024**3, 2),
        "by_type": by_type,
    }
    if snapshot is not None:
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(orjson.dumps(payload, option=orjson.OPT_INDENT_2))
    _json(payload)
