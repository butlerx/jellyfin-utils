"""Shared data models for the Jellyfin API client."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass


def _extract_size(item: dict) -> int:
    """Sum file sizes from ``MediaSources`` (the only reliable source for on-disk size)."""
    sources = item.get("MediaSources")
    if not sources:
        return 0
    return sum(s.get("Size", 0) or 0 for s in sources)


@dataclass(frozen=True)
class LibraryItem:
    """Immutable representation of a Jellyfin library item."""

    item_id: str
    name: str
    item_type: str
    path: str
    size: int
    series_name: str | None
    series_id: str | None
    parent_index: int | None
    episode_index: int | None
    date_created: dt.datetime | None
    tmdb_id: int | None
    production_year: int | None
    size_is_rollup: bool = False
    """``True`` when ``size`` is the total of child items rather than this item's own files."""

    @classmethod
    def from_api(cls, raw: dict) -> LibraryItem | None:
        """Build from an API response dict, returning ``None`` for items without an ID."""
        item_id = raw.get("Id")
        if not item_id:
            return None

        date_str = raw.get("DateCreated")
        date_created = dt.datetime.fromisoformat(date_str) if date_str else None

        provider_ids = raw.get("ProviderIds") or {}
        series_provider_ids = raw.get("SeriesProviderIds") or {}
        tmdb_id = provider_ids.get("Tmdb") or series_provider_ids.get("Tmdb")

        return cls(
            item_id=item_id,
            name=raw.get("Name", "<no name>"),
            item_type=raw.get("Type", "Unknown"),
            path=raw.get("Path", ""),
            size=_extract_size(raw),
            series_name=raw.get("SeriesName"),
            series_id=raw.get("SeriesId"),
            parent_index=raw.get("ParentIndexNumber"),
            episode_index=raw.get("IndexNumber"),
            date_created=date_created,
            tmdb_id=int(tmdb_id) if tmdb_id is not None else None,
            production_year=raw.get("ProductionYear"),
        )


def display_name(item: LibraryItem) -> str:
    """Build a human-readable name, prefixing episodes with series and season info."""
    if item.item_type != "Episode":
        return item.name

    parts = []
    if item.series_name:
        parts.append(item.series_name)
    if item.parent_index is not None and item.episode_index is not None:
        parts.append(f"S{item.parent_index:02d}E{item.episode_index:02d}")
    parts.append(item.name)
    return " - ".join(parts)


def size_gb(size_bytes: int) -> float:
    """Convert a byte count to gibibytes."""
    return size_bytes / (1024**3)
