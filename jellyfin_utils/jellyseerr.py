"""Small client for the read-only Jellyseerr request API."""

from __future__ import annotations

from typing import TYPE_CHECKING

from jellyfin_utils.http import request_json

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ["get_requesters_by_tmdb_id", "get_requests"]

SERVICE = "Jellyseerr"
PAGE_SIZE = 100
TIMEOUT = 30


def _iter_requests(base_url: str, api_key: str) -> Iterator[dict]:
    """Yield every Jellyseerr request record, one page at a time."""
    headers = {"X-Api-Key": api_key}
    skip = 0
    while True:
        payload = request_json(
            "GET",
            f"{base_url}/api/v1/request",
            service=SERVICE,
            headers=headers,
            params={"take": PAGE_SIZE, "skip": skip, "filter": "all"},
            timeout=TIMEOUT,
        )
        page = payload.get("results") or []
        if not page:
            return
        yield from page
        skip += len(page)
        total = (payload.get("pageInfo") or {}).get("results", 0)
        if skip >= total:
            return


def get_requests(base_url: str, api_key: str) -> list[dict]:
    """Return all Jellyseerr requests with fields useful for reconciliation."""
    results = []
    for request in _iter_requests(base_url, api_key):
        media = request.get("media") or {}
        results.append(
            {
                "id": request.get("id"),
                "status": request.get("status"),
                "requested_by": (request.get("requestedBy") or {}).get("username"),
                "tmdb_id": media.get("tmdbId"),
                "media_type": media.get("mediaType"),
            }
        )
    return results


def get_requesters_by_tmdb_id(base_url: str, api_key: str) -> dict[int, tuple[str, ...]]:
    """Return Jellyseerr requesters indexed by TMDb ID."""
    requesters: dict[int, set[str]] = {}
    for request in _iter_requests(base_url, api_key):
        media = request.get("media") or {}
        tmdb_id = media.get("tmdbId")
        username = (request.get("requestedBy") or {}).get("username")
        if tmdb_id is not None and username:
            requesters.setdefault(int(tmdb_id), set()).add(username)
    return {tmdb_id: tuple(sorted(names)) for tmdb_id, names in requesters.items()}
