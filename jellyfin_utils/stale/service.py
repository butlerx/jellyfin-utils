"""Selection logic for stale-media analysis."""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from .models import StaleItem

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from jellyfin_utils.client import LibraryItem


def find_stale(
    all_items: Sequence[LibraryItem],
    watchers: Mapping[str, Sequence[str]],
    total_active_users: int,
    max_watchers: int,
    min_age_days: int | None,
    now: dt.datetime,
    requesters_by_tmdb_id: Mapping[int, tuple[str, ...]] | None = None,
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
        item_watchers = watchers.get(item.item_id, ())
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

    return sorted(
        results,
        key=lambda stale_item: (
            not stale_item.requester_watched,
            -stale_item.item.size,
            stale_item.watch_count,
        ),
    )
