"""Small client for Jellyseerr requests and user management."""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

from jellyfin_utils.http import request_json

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = [
    "check_jellyseerr_user_management_access",
    "find_linked_jellyseerr_user",
    "get_jellyseerr_users",
    "get_requesters_by_tmdb_id",
    "get_requests",
    "import_jellyfin_user",
    "update_jellyseerr_profile",
]

SERVICE = "Jellyseerr"
PAGE_SIZE = 100
TIMEOUT = 30
_ADMIN_PERMISSION = 2
_MANAGE_USERS_PERMISSION = 8


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
        if not isinstance(payload, dict):
            return
        page = payload.get("results") or []
        if not isinstance(page, list) or not page:
            return
        for request in page:
            if isinstance(request, dict):
                yield request
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
        if tmdb_id is None or not username:
            continue
        try:
            parsed_tmdb_id = int(tmdb_id)
        except (TypeError, ValueError):
            continue
        requesters.setdefault(parsed_tmdb_id, set()).add(username)
    return {tmdb_id: tuple(sorted(names)) for tmdb_id, names in requesters.items()}


def get_jellyseerr_users(base_url: str, api_key: str, query: str) -> list[dict]:
    """Find Jellyseerr users matching an email, display name, or media-server name."""
    headers = {"X-Api-Key": api_key}
    results = []
    skip = 0
    while True:
        payload = request_json(
            "GET",
            f"{base_url}/api/v1/user",
            service=SERVICE,
            headers=headers,
            params={"take": PAGE_SIZE, "skip": skip, "q": query},
            timeout=TIMEOUT,
        )
        if not isinstance(payload, dict):
            return results
        page = payload.get("results") or []
        if not isinstance(page, list) or not page:
            return results
        results.extend(user for user in page if isinstance(user, dict))
        skip += len(page)
        total = (payload.get("pageInfo") or {}).get("results", 0)
        if not isinstance(total, int) or skip >= total:
            return results


def check_jellyseerr_user_management_access(base_url: str, api_key: str) -> None:
    """Verify the API key's effective account can manage users without mutating Jellyseerr."""
    profile = request_json(
        "GET",
        f"{base_url}/api/v1/auth/me",
        service=SERVICE,
        headers={"X-Api-Key": api_key},
        timeout=TIMEOUT,
    )
    if not isinstance(profile, dict):
        message = "Jellyseerr returned an invalid current-user response for the supplied API key."
        raise click.ClickException(message)

    permissions = profile.get("permissions")
    if isinstance(permissions, int) and permissions & (_ADMIN_PERMISSION | _MANAGE_USERS_PERMISSION):
        return

    identity = profile.get("username") or profile.get("email") or profile.get("id") or "unknown user"
    message = (
        f'Jellyseerr API key authenticates as "{identity}", but that account lacks '
        "Admin or Manage Users permission."
    )
    raise click.ClickException(message)


def find_linked_jellyseerr_user(
    base_url: str,
    api_key: str,
    jellyfin_user_id: str,
    username: str,
) -> dict | None:
    """Find the Jellyseerr account linked to a Jellyfin user ID."""
    return next(
        (
            user
            for user in get_jellyseerr_users(base_url, api_key, username)
            if str(user.get("jellyfinUserId") or "") == jellyfin_user_id
        ),
        None,
    )


def import_jellyfin_user(base_url: str, api_key: str, jellyfin_user_id: str) -> dict | None:
    """Import one Jellyfin account into Jellyseerr and return the linked user."""
    imported = request_json(
        "POST",
        f"{base_url}/api/v1/user/import-from-jellyfin",
        service=SERVICE,
        headers={"X-Api-Key": api_key},
        json={"jellyfinUserIds": [jellyfin_user_id]},
        timeout=TIMEOUT,
    )
    if not isinstance(imported, list):
        return None
    return next(
        (
            user
            for user in imported
            if isinstance(user, dict) and user.get("jellyfinUserId") == jellyfin_user_id
        ),
        next((user for user in imported if isinstance(user, dict)), None),
    )


def update_jellyseerr_profile(
    base_url: str,
    api_key: str,
    user_id: int,
    username: str,
    email: str,
) -> dict:
    """Set the display name and email on a linked Jellyseerr user."""
    headers = {"X-Api-Key": api_key}
    url = f"{base_url}/api/v1/user/{user_id}/settings/main"
    current = request_json("GET", url, service=SERVICE, headers=headers, timeout=TIMEOUT)
    body = {
        **(current if isinstance(current, dict) else {}),
        "username": username,
        "email": email,
    }
    updated = request_json(
        "POST",
        url,
        service=SERVICE,
        headers=headers,
        json=body,
        timeout=TIMEOUT,
    )
    return updated if isinstance(updated, dict) else body
