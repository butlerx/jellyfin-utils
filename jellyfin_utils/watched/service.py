"""Candidate selection for watched-media analysis."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .models import Candidate

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from jellyfin_utils.client import LibraryItem


def _make_candidate(
    item: LibraryItem,
    watchers: Mapping[str, Sequence[str]],
    total_active_users: int,
    requesters_by_tmdb_id: Mapping[int, tuple[str, ...]] | None = None,
) -> Candidate | None:
    """Build a ``Candidate`` if the item has watchers, otherwise ``None``."""
    item_watchers = watchers.get(item.item_id, ())
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
    all_items: Sequence[LibraryItem],
    watchers: Mapping[str, Sequence[str]],
    total_active_users: int,
    threshold_percent: int,
    requesters_by_tmdb_id: Mapping[int, tuple[str, ...]] | None = None,
) -> list[Candidate]:
    """Filter on-disk items, enrich them with watch data, apply threshold, and sort."""
    threshold_count = (threshold_percent / 100.0) * total_active_users
    return sorted(
        (
            candidate
            for item in all_items
            if item.path
            and (
                candidate := _make_candidate(
                    item,
                    watchers,
                    total_active_users,
                    requesters_by_tmdb_id,
                )
            )
            is not None
            and candidate.watch_count >= threshold_count
        ),
        key=lambda candidate: (
            not candidate.requester_watched,
            -candidate.item.size,
            -candidate.watch_percentage,
        ),
    )
