"""CLI for jellyfin-stale."""

from __future__ import annotations

import datetime as dt

import click

from jellyfin_utils.client import (
    LibraryItem,
    build_headers,
    get_all_items,
    get_users,
    get_watchers_per_item,
)
from jellyfin_utils.jellyseerr import get_requesters_by_tmdb_id
from jellyfin_utils.options import (
    connection_options,
    ignore_user_option,
    jellyseerr_options,
    output_option,
    quiet_option,
    require_jellyseerr_pair,
)
from jellyfin_utils.output import OutputFormat

from .models import StaleItem
from .render import MEDIA_TYPE_ORDER, render_csv, render_json, render_markdown, render_text


def find_stale(
    all_items: list[LibraryItem],
    watchers: dict[str, list[str]],
    total_active_users: int,
    max_watchers: int,
    min_age_days: int | None,
    now: dt.datetime,
    requesters_by_tmdb_id: dict[int, tuple[str, ...]] | None = None,
) -> list[StaleItem]:
    """Filter items to those with at most ``max_watchers``, respecting minimum age."""
    min_age_cutoff = now - dt.timedelta(days=min_age_days) if min_age_days is not None else None

    results: list[StaleItem] = []
    for item in all_items:
        if not item.path:
            continue
        if (
            min_age_cutoff is not None
            and item.date_created is not None
            and item.date_created > min_age_cutoff
        ):
            continue
        item_watchers = watchers.get(item.item_id, [])
        count = len(item_watchers)
        if count <= max_watchers:
            requested_by = (
                requesters_by_tmdb_id.get(item.tmdb_id, ())
                if requesters_by_tmdb_id is not None and item.tmdb_id is not None
                else ()
            )
            watcher_names = {username.casefold() for username in item_watchers}
            watched_by_requester = tuple(
                username for username in requested_by if username.casefold() in watcher_names
            )
            results.append(
                StaleItem.build(
                    item,
                    count,
                    total_active_users,
                    now,
                    requested_by=requested_by,
                    watched_by_requester=watched_by_requester,
                )
            )

    return sorted(results, key=lambda s: (not s.requester_watched, -s.item.size, s.watch_count))


def group_by_type(stale_items: list[StaleItem]) -> dict[str, list[StaleItem]]:
    """Group stale items by media type, preserving sort order."""
    grouped: dict[str, list[StaleItem]] = {t: [] for t in MEDIA_TYPE_ORDER}
    for s in stale_items:
        if s.item.item_type in grouped:
            grouped[s.item.item_type].append(s)
    return grouped


@click.command("stale")
@connection_options
@jellyseerr_options
@ignore_user_option
@click.option("--min-age", type=int, default=None, help="Only flag items added more than N days ago.")
@click.option(
    "--max-watchers",
    type=int,
    default=0,
    show_default=True,
    help="Maximum number of users who watched an item for it to count as stale.",
)
@output_option
@quiet_option
def main(
    base_url: str,
    token: str,
    ignore_user: tuple[str, ...],
    min_age: int | None,
    max_watchers: int,
    jellyseerr_server: str | None,
    jellyseerr_token: str | None,
    output_format: OutputFormat,
    quiet: bool,
) -> None:
    """Find unwatched or rarely-watched Jellyfin content."""
    require_jellyseerr_pair(jellyseerr_server, jellyseerr_token)

    headers = build_headers(token)

    users = get_users(base_url, headers)
    ignore_usernames = set(ignore_user)

    watchers = get_watchers_per_item(base_url, headers, users, ignore_usernames, max_age_days=None)

    active_user_count = sum(1 for u in users if u.get("Name") not in ignore_usernames)
    all_items = get_all_items(base_url, headers)
    now = dt.datetime.now(dt.UTC)
    requesters_by_tmdb_id = (
        get_requesters_by_tmdb_id(jellyseerr_server, jellyseerr_token)
        if jellyseerr_server and jellyseerr_token
        else None
    )
    stale_items = find_stale(
        all_items, watchers, active_user_count, max_watchers, min_age, now, requesters_by_tmdb_id
    )
    grouped = group_by_type(stale_items)

    match output_format:
        case OutputFormat.JSON:
            click.echo(
                render_json(
                    stale_items,
                    grouped,
                    base_url=base_url,
                    total_users=len(users),
                    ignore_usernames=ignore_usernames,
                    total_active_users=active_user_count,
                    max_watchers=max_watchers,
                    total_items=len(all_items),
                    min_age_days=min_age,
                    jellyseerr_enabled=requesters_by_tmdb_id is not None,
                )
            )
        case OutputFormat.CSV:
            click.echo(render_csv(stale_items), nl=False)
        case OutputFormat.MARKDOWN:
            click.echo(render_markdown(stale_items, active_user_count))
        case _:
            click.echo(
                render_text(
                    stale_items,
                    grouped,
                    active_user_count,
                    quiet=quiet,
                    total_users=len(users),
                    ignore_usernames=ignore_usernames,
                    max_watchers=max_watchers,
                    total_items=len(all_items),
                    min_age_days=min_age,
                    jellyseerr_enabled=requesters_by_tmdb_id is not None,
                )
            )
