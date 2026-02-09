"""CLI for jellyfin-stale."""

from __future__ import annotations

import datetime as dt

import click

from jellyfin_utils.client import (
    LibraryItem,
    build_headers,
    get_all_items,
    get_users,
    get_watch_counts_per_item,
)

from .models import StaleItem
from .render import MEDIA_TYPE_ORDER, render_csv, render_json, render_text


def find_stale(
    all_items: list[LibraryItem],
    watch_counts: dict[str, int],
    total_active_users: int,
    max_watchers: int,
    min_age_days: int | None,
    now: dt.datetime,
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
        count = watch_counts.get(item.item_id, 0)
        if count <= max_watchers:
            results.append(StaleItem.build(item, count, total_active_users, now))

    return sorted(results, key=lambda s: (-s.item.size, s.watch_count))


def group_by_type(stale_items: list[StaleItem]) -> dict[str, list[StaleItem]]:
    """Group stale items by media type, preserving sort order."""
    grouped: dict[str, list[StaleItem]] = {t: [] for t in MEDIA_TYPE_ORDER}
    for s in stale_items:
        if s.item.item_type in grouped:
            grouped[s.item.item_type].append(s)
    return grouped


@click.command("jellyfin-stale")
@click.option(
    "--server",
    envvar="JELLYFIN_SERVER",
    required=True,
    help="Jellyfin server base URL (e.g. http://jellyfin.lan:8096).",
)
@click.option(
    "--token",
    envvar="JELLYFIN_TOKEN",
    required=True,
    help="Jellyfin API key.",
)
@click.option(
    "--ignore-user",
    multiple=True,
    help="Username to ignore (can be passed multiple times).",
)
@click.option(
    "--min-age",
    type=int,
    default=None,
    help="Only flag items added more than N days ago (skip recent additions).",
)
@click.option(
    "--max-watchers",
    type=int,
    default=0,
    show_default=True,
    help="Maximum number of users who watched an item for it to be considered stale.",
)
@click.option(
    "--output",
    "output_format",
    type=click.Choice(["text", "json", "csv"], case_sensitive=False),
    default="text",
    show_default=True,
    help="Output format.",
)
@click.option(
    "--quiet",
    is_flag=True,
    help="In text mode, only print the list of stale items, no summary.",
)
def main(
    server: str,
    token: str,
    ignore_user: tuple[str, ...],
    min_age: int | None,
    max_watchers: int,
    output_format: str,
    quiet: bool,  # noqa: FBT001
) -> None:
    """Find unwatched or rarely-watched Jellyfin content."""
    base_url = server.rstrip("/")
    headers = build_headers(token)

    users = get_users(base_url, headers)
    ignore_usernames = set(ignore_user)

    watch_counts = get_watch_counts_per_item(base_url, headers, users, ignore_usernames)

    active_user_count = sum(1 for u in users if u.get("Name") not in ignore_usernames)
    all_items = get_all_items(base_url, headers)
    now = dt.datetime.now(dt.UTC)
    stale_items = find_stale(all_items, watch_counts, active_user_count, max_watchers, min_age, now)
    grouped = group_by_type(stale_items)

    match output_format:
        case "json":
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
                )
            )
        case "csv":
            click.echo(render_csv(stale_items), nl=False)
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
                )
            )


if __name__ == "__main__":
    main()
