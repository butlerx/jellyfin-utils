"""Shared fixtures and builders for the test suite."""

from __future__ import annotations

from typing import Any

import pytest

from jellyfin_utils.client import LibraryItem

BASE_URL = "http://jellyfin.test"
JELLYSEERR_URL = "http://jellyseerr.test"


def make_item(
    item_id: str,
    item_type: str = "Movie",
    *,
    name: str | None = None,
    path: str = "/media/file.mkv",
    size: int = 0,
    series_name: str | None = None,
    series_id: str | None = None,
    tmdb_id: int | None = None,
) -> LibraryItem:
    """Build a ``LibraryItem`` with only the fields a given test cares about."""
    return LibraryItem(
        item_id=item_id,
        name=name if name is not None else item_id,
        item_type=item_type,
        path=path,
        size=size,
        series_name=series_name,
        series_id=series_id,
        parent_index=None,
        episode_index=None,
        date_created=None,
        tmdb_id=tmdb_id,
        production_year=None,
    )


@pytest.fixture
def headers() -> dict[str, str]:
    """Auth headers for the fake Jellyfin server."""
    return {"X-MediaBrowser-Token": "test-token"}


def items_page(items: list[dict[str, Any]], total: int) -> dict[str, Any]:
    """Build one page of a Jellyfin ``/Items`` response."""
    return {"Items": items, "TotalRecordCount": total}
