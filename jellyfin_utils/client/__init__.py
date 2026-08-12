"""Shared Jellyfin API client helpers."""

from __future__ import annotations

import datetime as dt

import orjson
import requests

from .models import LibraryItem, display_name, size_gb

__all__ = [
    "LibraryItem",
    "build_headers",
    "display_name",
    "get_all_items",
    "get_users",
    "get_watch_counts_per_item",
    "get_watchers_per_item",
    "parse_last_played",
    "size_gb",
]


def build_headers(token: str) -> dict[str, str]:
    """Build Jellyfin auth headers."""
    return {"X-MediaBrowser-Token": token}


def get_users(base_url: str, headers: dict[str, str]) -> list[dict]:
    """Fetch all users from the Jellyfin server."""
    r = requests.get(f"{base_url}/Users", headers=headers, timeout=15)
    r.raise_for_status()
    return orjson.loads(r.content)


def parse_last_played(item: dict) -> dt.datetime | None:
    """Extract ``LastPlayedDate`` from an item's ``UserData``, if present."""
    user_data = item.get("UserData") or {}
    date_str = user_data.get("LastPlayedDate")
    if not date_str:
        return None
    return dt.datetime.fromisoformat(date_str)


def _is_played_recently(item: dict, cutoff: dt.datetime | None) -> bool:
    """Check whether an item was played after the cutoff (or always ``True`` if no cutoff)."""
    if cutoff is None:
        return True
    last_played = parse_last_played(item)
    return last_played is not None and last_played >= cutoff


def get_all_items(
    base_url: str,
    headers: dict[str, str],
    *,
    include_types: str = "Movie,Series,Episode",
    fields: str = (
        "Path,MediaSources,SeriesName,SeasonName,IndexNumber,ParentIndexNumber,DateCreated,"
        "ProviderIds,SeriesProviderIds"
    ),
) -> list[LibraryItem]:
    """Fetch library items as immutable ``LibraryItem`` instances."""
    params = {
        "IncludeItemTypes": include_types,
        "Recursive": "true",
        "EnableUserData": "false",
        "Fields": fields,
        "Limit": 100000,
    }
    r = requests.get(f"{base_url}/Items", headers=headers, params=params, timeout=60)
    r.raise_for_status()
    data = orjson.loads(r.content)
    return [item for raw in data.get("Items", []) if (item := LibraryItem.from_api(raw)) is not None]


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
            "Limit": 100000,
        }
        r = requests.get(
            f"{base_url}/Items",
            headers=headers,
            params=params,
            timeout=60,
        )
        r.raise_for_status()
        data = orjson.loads(r.content)

        for item in data.get("Items", []):
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
