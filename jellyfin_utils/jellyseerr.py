"""Small client for the read-only Jellyseerr request API."""

from __future__ import annotations

import orjson
import requests


def get_requests(base_url: str, api_key: str) -> list[dict]:
    """Return all Jellyseerr requests with fields useful for reconciliation."""
    headers = {"X-Api-Key": api_key}
    results: list[dict] = []
    skip = 0
    while True:
        response = requests.get(
            f"{base_url}/api/v1/request",
            headers=headers,
            params={"take": 100, "skip": skip, "filter": "all"},
            timeout=30,
        )
        response.raise_for_status()
        payload = orjson.loads(response.content)
        page = payload.get("results", [])
        for request in page:
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
        skip += len(page)
        if not page or skip >= (payload.get("pageInfo") or {}).get("results", 0):
            return results


def get_requesters_by_tmdb_id(base_url: str, api_key: str) -> dict[int, tuple[str, ...]]:
    """Return Jellyseerr requesters indexed by TMDb ID."""
    headers = {"X-Api-Key": api_key}
    requesters: dict[int, set[str]] = {}
    skip = 0
    take = 100

    while True:
        response = requests.get(
            f"{base_url}/api/v1/request",
            headers=headers,
            params={"take": take, "skip": skip, "filter": "all"},
            timeout=30,
        )
        response.raise_for_status()
        payload = orjson.loads(response.content)
        results = payload.get("results", [])

        for request in results:
            media = request.get("media") or {}
            tmdb_id = media.get("tmdbId")
            username = (request.get("requestedBy") or {}).get("username")
            if tmdb_id is not None and username:
                requesters.setdefault(int(tmdb_id), set()).add(username)

        page_info = payload.get("pageInfo") or {}
        total_results = page_info.get("results", 0)
        skip += len(results)
        if not results or skip >= total_results:
            break

    return {tmdb_id: tuple(sorted(names)) for tmdb_id, names in requesters.items()}
