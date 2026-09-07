"""CLI for jellyfin-watched."""

from __future__ import annotations

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
    threshold_option,
)

from .render import build_render_data, render
from .service import find_candidates

if TYPE_CHECKING:
    from jellyfin_utils.output import OutputFormat

__all__ = ["find_candidates", "main"]


@click.command("watched")
@connection_options
@jellyseerr_options
@ignore_user_option
@click.option("--days", type=int, default=None, help="Only count plays within the last N days as watched.")
@threshold_option
@output_option
@quiet_option
def main(
    base_url: str,
    token: str,
    ignore_user: tuple[str, ...],
    days: int | None,
    threshold: int,
    jellyseerr_server: str | None,
    jellyseerr_token: str | None,
    output_format: OutputFormat,
    quiet: bool,
) -> None:
    """Analyze Jellyfin usage and list media safe to delete."""
    context = load_media_analysis(
        base_url,
        token,
        ignore_user,
        max_watch_age_days=days,
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
    data = build_render_data(
        candidates,
        context,
        threshold=threshold,
        max_age_days=days,
        quiet=quiet,
    )
    render(data, output_format)
