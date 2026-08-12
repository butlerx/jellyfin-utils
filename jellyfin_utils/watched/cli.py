"""Analysis logic and CLI for jellyfin-watched."""

from __future__ import annotations

import click

from jellyfin_utils.client import LibraryItem, build_headers, get_all_items, get_users, get_watchers_per_item
from jellyfin_utils.jellyseerr import get_requesters_by_tmdb_id

from .models import Candidate
from .render import MEDIA_TYPE_ORDER, render_csv, render_json, render_markdown, render_text


def _make_candidate(
    item: LibraryItem,
    watchers: dict[str, list[str]],
    total_active_users: int,
    requesters_by_tmdb_id: dict[int, tuple[str, ...]] | None = None,
) -> Candidate | None:
    """Build a ``Candidate`` if the item has watchers, otherwise ``None``."""
    item_watchers = watchers.get(item.item_id, [])
    if not item_watchers:
        return None
    watch_pct = (len(item_watchers) / total_active_users * 100) if total_active_users > 0 else 0
    requested_by = (
        requesters_by_tmdb_id.get(item.tmdb_id, ())
        if requesters_by_tmdb_id is not None and item.tmdb_id is not None
        else ()
    )
    watcher_names = {username.casefold() for username in item_watchers}
    watched_by_requester = tuple(
        username for username in requested_by if username.casefold() in watcher_names
    )
    return Candidate(
        item=item,
        watch_count=len(item_watchers),
        watch_percentage=round(watch_pct, 1),
        watched_by=tuple(sorted(item_watchers)),
        requested_by=requested_by,
        watched_by_requester=watched_by_requester,
    )


def find_candidates(
    all_items: list[LibraryItem],
    watchers: dict[str, list[str]],
    total_active_users: int,
    threshold_percent: int,
    requesters_by_tmdb_id: dict[int, tuple[str, ...]] | None = None,
) -> list[Candidate]:
    """Pure pipeline: filter on-disk items → enrich with watch data → threshold → sort."""
    threshold_count = (threshold_percent / 100.0) * total_active_users
    return sorted(
        (
            c
            for item in all_items
            if item.path
            and (c := _make_candidate(item, watchers, total_active_users, requesters_by_tmdb_id)) is not None
            and c.watch_count >= threshold_count
        ),
        key=lambda c: (not c.requester_watched, -c.item.size, -c.watch_percentage),
    )


def group_by_type(candidates: list[Candidate]) -> dict[str, list[Candidate]]:
    """Group candidates by media type, preserving sort order."""
    grouped: dict[str, list[Candidate]] = {t: [] for t in MEDIA_TYPE_ORDER}
    for candidate in candidates:
        if candidate.item.item_type in grouped:
            grouped[candidate.item.item_type].append(candidate)
    return grouped


@click.command("jellyfin-watched")
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
    "--days",
    type=int,
    default=None,
    help="Only consider plays within the last N days as 'watched'.",
)
@click.option(
    "--threshold",
    type=int,
    default=80,
    show_default=True,
    help="Percentage of users who must have watched an item for it to be a candidate.",
)
@click.option(
    "--jellyseerr-server",
    envvar="JELLYSEERR_SERVER",
    help="Jellyseerr server base URL; enables requester-watch prioritization.",
)
@click.option(
    "--jellyseerr-token",
    envvar="JELLYSEERR_TOKEN",
    help="Jellyseerr API key; required with --jellyseerr-server.",
)
@click.option(
    "--output",
    "output_format",
    type=click.Choice(["text", "json", "csv", "markdown"], case_sensitive=False),
    default="text",
    show_default=True,
    help="Output format.",
)
@click.option(
    "--quiet",
    is_flag=True,
    help="In text mode, only print the list of candidate items, no summary.",
)
def main(
    server: str,
    token: str,
    ignore_user: tuple[str, ...],
    days: int | None,
    threshold: int,
    jellyseerr_server: str | None,
    jellyseerr_token: str | None,
    output_format: str,
    quiet: bool,  # noqa: FBT001
) -> None:
    """Analyze Jellyfin usage and list media safe to delete."""
    if bool(jellyseerr_server) != bool(jellyseerr_token):
        message = "--jellyseerr-server and --jellyseerr-token must be used together."
        raise click.UsageError(message)

    base_url = server.rstrip("/")
    headers = build_headers(token)

    users = get_users(base_url, headers)
    ignore_usernames = set(ignore_user)

    watchers = get_watchers_per_item(
        base_url,
        headers,
        users,
        ignore_usernames,
        max_age_days=days,
    )

    active_user_count = sum(1 for u in users if u.get("Name") not in ignore_usernames)
    all_items = get_all_items(base_url, headers)
    requesters_by_tmdb_id = (
        get_requesters_by_tmdb_id(jellyseerr_server.rstrip("/"), jellyseerr_token)
        if jellyseerr_server and jellyseerr_token
        else None
    )
    candidates = find_candidates(all_items, watchers, active_user_count, threshold, requesters_by_tmdb_id)
    grouped = group_by_type(candidates)

    match output_format:
        case "json":
            click.echo(
                render_json(
                    candidates,
                    grouped,
                    base_url=base_url,
                    total_users=len(users),
                    ignore_usernames=ignore_usernames,
                    total_active_users=active_user_count,
                    threshold=threshold,
                    total_items=len(all_items),
                    max_age_days=days,
                    jellyseerr_enabled=requesters_by_tmdb_id is not None,
                )
            )
        case "csv":
            click.echo(render_csv(candidates), nl=False)
        case "markdown":
            click.echo(render_markdown(candidates, active_user_count))
        case _:
            click.echo(
                render_text(
                    candidates,
                    grouped,
                    active_user_count,
                    quiet=quiet,
                    total_users=len(users),
                    ignore_usernames=ignore_usernames,
                    threshold=threshold,
                    total_items=len(all_items),
                    max_age_days=days,
                    jellyseerr_enabled=requesters_by_tmdb_id is not None,
                )
            )


if __name__ == "__main__":
    main()
