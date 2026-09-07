"""Watcher filtering and aggregation helpers."""

from __future__ import annotations

import datetime as dt

from .library import parse_last_played
from .pagination import iter_items


def _is_played_recently(item: dict, cutoff: dt.datetime | None) -> bool:
    """Check whether an item was played after the cutoff (or always ``True`` if no cutoff)."""
    if cutoff is None:
        return True
    last_played = parse_last_played(item)
    return last_played is not None and last_played >= cutoff


def get_watchers_per_item(
    base_url: str,
    headers: dict[str, str],
    users: list[dict],
    ignore_usernames: set[str],
    max_age_days: int | None,
) -> dict[str, list[str]]:
    """Return a mapping of ``item_id`` to the list of usernames who watched it."""
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=max_age_days) if max_age_days is not None else None

    active_users = [
        (user.get("Id"), user.get("Name", "unknown"))
        for user in users
        if user.get("Name") not in ignore_usernames and user.get("Id")
    ]

    watchers: dict[str, list[str]] = {}

    for user_id, username in active_users:
        params = {
            "UserId": user_id,
            "IncludeItemTypes": "Movie,Series,Episode",
            "Recursive": "true",
            "Filters": "IsPlayed",
            "EnableUserData": "true",
        }

        for item in iter_items(base_url, headers, params):
            item_id = item.get("Id")
            if item_id and _is_played_recently(item, cutoff):
                watchers.setdefault(item_id, []).append(username)

    return watchers


def get_watch_counts_per_item(
    base_url: str,
    headers: dict[str, str],
    users: list[dict],
    ignore_usernames: set[str],
) -> dict[str, int]:
    """Return a mapping of ``item_id`` to how many users have watched it (all-time)."""
    watchers = get_watchers_per_item(base_url, headers, users, ignore_usernames, max_age_days=None)
    return {item_id: len(usernames) for item_id, usernames in watchers.items()}
