"""Library item mapping and series aggregation helpers."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace

from .models import LibraryItem
from .pagination import iter_items


def parse_last_played(item: dict) -> dt.datetime | None:
    """Extract ``LastPlayedDate`` from an item's ``UserData``, if present."""
    user_data = item.get("UserData") or {}
    date_str = user_data.get("LastPlayedDate")
    if not date_str:
        return None
    return dt.datetime.fromisoformat(date_str)


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
    }
    items = [
        item
        for raw in iter_items(base_url, headers, params)
        if (item := LibraryItem.from_api(raw)) is not None
    ]
    if "Series" in include_types and "Episode" in include_types:
        return roll_up_series_sizes(drop_empty_series(items))
    return items
