"""Data models for the jellyfin-stale analysis."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import datetime as dt

    from jellyfin_utils.client.models import LibraryItem


@dataclass(frozen=True)
class StaleItem:
    """A library item that has been watched by few or no users."""

    item: LibraryItem
    watch_count: int
    total_users: int
    age_days: int | None

    @property
    def watch_percentage(self) -> float:
        """Percentage of active users who watched this item."""
        if self.total_users == 0:
            return 0.0
        return round(self.watch_count / self.total_users * 100, 1)

    @classmethod
    def build(
        cls,
        item: LibraryItem,
        watch_count: int,
        total_users: int,
        now: dt.datetime,
    ) -> StaleItem:
        """Construct from a library item, computing age from ``date_created``."""
        age_days = (now - item.date_created).days if item.date_created else None
        return cls(
            item=item,
            watch_count=watch_count,
            total_users=total_users,
            age_days=age_days,
        )
