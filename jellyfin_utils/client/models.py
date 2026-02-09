"""Shared data models for the Jellyfin API client."""

from __future__ import annotations

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
    parent_index: int | None
    episode_index: int | None

    @classmethod
    def from_api(cls, raw: dict) -> LibraryItem | None:
        """Build from an API response dict, returning ``None`` for items without an ID."""
        item_id = raw.get("Id")
        if not item_id:
            return None
        return cls(
            item_id=item_id,
            name=raw.get("Name", "<no name>"),
            item_type=raw.get("Type", "Unknown"),
            path=raw.get("Path", ""),
            size=_extract_size(raw),
            series_name=raw.get("SeriesName"),
            parent_index=raw.get("ParentIndexNumber"),
            episode_index=raw.get("IndexNumber"),
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
