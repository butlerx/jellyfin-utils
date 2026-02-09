"""Data models for the jellyfin-watched analysis."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jellyfin_utils.client.models import LibraryItem


@dataclass(frozen=True)
class Candidate:
    """A library item that meets the watch threshold."""

    item: LibraryItem
    watch_count: int
    watch_percentage: float
    watched_by: tuple[str, ...]
