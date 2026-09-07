"""CLI for jellyfin-stale."""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

import click

# These client imports remain module attributes for callers that patch the historical CLI seams.
from jellyfin_utils.client import build_headers, get_all_items, get_users, get_watchers_per_item
from jellyfin_utils.jellyseerr import get_requesters_by_tmdb_id
from jellyfin_utils.media import load_media_analysis
from jellyfin_utils.options import (
    connection_options,
    ignore_user_option,
    jellyseerr_options,
    output_option,
    quiet_option,
)

from .render import build_render_data, render
from .service import find_stale

if TYPE_CHECKING:
    from jellyfin_utils.output import OutputFormat

__all__ = ["find_stale", "main"]


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
    stale_items = find_stale(
        context.items,
        context.watchers,
        context.active_user_count,
        max_watchers,
        min_age,
        dt.datetime.now(dt.UTC),
        context.requesters_by_tmdb_id,
    )
    data = build_render_data(
        stale_items,
        context,
        max_watchers=max_watchers,
        min_age_days=min_age,
        quiet=quiet,
    )
    render(data, output_format)
