"""Shared Jellyfin API client helpers."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace

import orjson
import requests

from .models import LibraryItem, display_name, size_gb

__all__ = [
    "LibraryItem",
    "build_headers",
    "create_user",
    "display_name",
    "drop_empty_series",
    "get_all_items",
    "get_json",
    "get_users",
    "get_watch_counts_per_item",
    "get_watchers_per_item",
    "parse_last_played",
    "post_empty",
    "roll_up_series_sizes",
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


def create_user(
    base_url: str,
    headers: dict[str, str],
    username: str,
    password: str | None,
) -> dict:
    """Create a Jellyfin user and return the server's user record."""
    response = requests.post(
        f"{base_url}/Users/New",
        headers=headers,
        json={"Name": username, "Password": password},
        timeout=15,
    )
    response.raise_for_status()
    return orjson.loads(response.content)


def get_json(base_url: str, headers: dict[str, str], path: str, *, params: dict | None = None) -> object:
    """Fetch and decode a JSON API response."""
    response = requests.get(f"{base_url}{path}", headers=headers, params=params, timeout=60)
    response.raise_for_status()
    return orjson.loads(response.content)


def post_empty(base_url: str, headers: dict[str, str], path: str) -> None:
    """Call an API endpoint that accepts no body and returns no content."""
    response = requests.post(f"{base_url}{path}", headers=headers, timeout=60)
    response.raise_for_status()


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


def drop_empty_series(items: list[LibraryItem]) -> list[LibraryItem]:
    """Drop ``Series`` entries that have no episodes left in the library."""
    # A series whose episodes were all deleted keeps its record, and often its folder, so it
    # would otherwise show up in every cleanup report as a zero-byte, never-watched item.
    series_with_episodes: set[str] = set()
    series_names_with_episodes: set[str] = set()
    for item in items:
        if item.item_type != "Episode":
            continue
        if item.series_id:
            series_with_episodes.add(item.series_id)
        if item.series_name:
            series_names_with_episodes.add(item.series_name.casefold())

    return [
        item
        for item in items
        if item.item_type != "Series"
        or item.item_id in series_with_episodes
        or item.name.casefold() in series_names_with_episodes
    ]


def roll_up_series_sizes(items: list[LibraryItem]) -> list[LibraryItem]:
    """Give each ``Series`` the combined size of its episodes."""
    # Series records carry no MediaSources, so their own size is always zero; the bytes sit on the
    # episodes underneath them. Rolled-up sizes are flagged so totals can skip them.
    by_series_id: dict[str, int] = {}
    by_series_name: dict[str, int] = {}
    for item in items:
        if item.item_type != "Episode":
            continue
        if item.series_id:
            by_series_id[item.series_id] = by_series_id.get(item.series_id, 0) + item.size
        if item.series_name:
            name = item.series_name.casefold()
            by_series_name[name] = by_series_name.get(name, 0) + item.size

    rolled: list[LibraryItem] = []
    for item in items:
        if item.item_type != "Series":
            rolled.append(item)
            continue
        total = by_series_id.get(item.item_id) or by_series_name.get(item.name.casefold(), 0)
        rolled.append(replace(item, size=total, size_is_rollup=True) if total else item)
    return rolled


def get_all_items(
    base_url: str,
    headers: dict[str, str],
    *,
    include_types: str = "Movie,Series,Episode",
    fields: str = (
        "Path,MediaSources,SeriesName,SeasonName,IndexNumber,ParentIndexNumber,DateCreated,"
        "ProviderIds,SeriesProviderIds,ProductionYear"
    ),
) -> list[LibraryItem]:
    """Fetch library items as immutable ``LibraryItem`` instances, minus episode-less series."""
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
    items = [item for raw in data.get("Items", []) if (item := LibraryItem.from_api(raw)) is not None]
    if "Series" in include_types and "Episode" in include_types:
        return roll_up_series_sizes(drop_empty_series(items))
    return items


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
