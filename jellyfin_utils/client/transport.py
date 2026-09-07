"""Direct Jellyfin API transport operations."""

from __future__ import annotations

from jellyfin_utils.http import request_empty, request_json

SERVICE = "Jellyfin"


def build_headers(token: str) -> dict[str, str]:
    """Build Jellyfin auth headers."""
    return {"X-MediaBrowser-Token": token}


def get_users(base_url: str, headers: dict[str, str]) -> list[dict]:
    """Fetch all users from the Jellyfin server."""
    return request_json("GET", f"{base_url}/Users", service=SERVICE, headers=headers, timeout=15)


def create_user(
    base_url: str,
    headers: dict[str, str],
    username: str,
    password: str | None,
) -> dict:
    """Create a Jellyfin user and return the server's user record."""
    return request_json(
        "POST",
        f"{base_url}/Users/New",
        service=SERVICE,
        headers=headers,
        json={"Name": username, "Password": password},
        timeout=15,
    )


def get_json(base_url: str, headers: dict[str, str], path: str, *, params: dict | None = None) -> object:
    """Fetch and decode a JSON API response."""
    return request_json("GET", f"{base_url}{path}", service=SERVICE, headers=headers, params=params)


def post_empty(base_url: str, headers: dict[str, str], path: str) -> None:
    """Call an API endpoint that accepts no body and returns no content."""
    request_empty("POST", f"{base_url}{path}", service=SERVICE, headers=headers)
