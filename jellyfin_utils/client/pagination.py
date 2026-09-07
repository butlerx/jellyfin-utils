"""Pagination helpers for Jellyfin item queries."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from .transport import get_json

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

# Jellyfin happily accepts a huge `Limit`, but a single unbounded response times out on large
# libraries and hides truncation, so /Items is walked one page at a time instead.
PAGE_SIZE = 1000


def iter_items(
    base_url: str,
    headers: dict[str, str],
    params: Mapping[str, Any],
) -> Iterator[dict]:
    """
    Yield every ``/Items`` record matching ``params``, one page at a time.

    Stops when a page comes back empty or ``TotalRecordCount`` has been reached,
    so a library larger than one page is never silently truncated.
    """
    start = 0
    while True:
        payload = cast(
            "Mapping[str, Any]",
            get_json(
                base_url,
                headers,
                "/Items",
                params={**params, "Limit": PAGE_SIZE, "StartIndex": start},
            ),
        )
        page = payload.get("Items") or []
        if not page:
            return
        yield from page
        start += len(page)
        total = payload.get("TotalRecordCount")
        if total is not None and start >= total:
            return
