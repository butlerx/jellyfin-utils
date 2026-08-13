"""
Shared HTTP plumbing for the Jellyfin and Jellyseerr clients.

Every API call goes through :func:`request_json` or :func:`request_empty`, which
turn transport and protocol failures into :class:`click.ClickException`. Click
prints that as ``Error: <message>`` and exits 1, so a wrong token or an
unreachable server gives the operator a single readable line instead of a
traceback.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import click
import orjson
import requests

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["DEFAULT_TIMEOUT", "request_empty", "request_json"]

DEFAULT_TIMEOUT = 60

# Status codes worth explaining, because the cause is nearly always the API key.
_AUTH_HINTS = {
    HTTPStatus.UNAUTHORIZED: "check the API key",
    HTTPStatus.FORBIDDEN: "the API key lacks permission for this operation",
}


def _describe_http_error(error: requests.HTTPError, service: str) -> str:
    response = error.response
    if response is None:
        return f"{service} request failed: {error}"
    status = response.status_code
    reason = response.reason or "error"
    detail = f"{service} returned {status} {reason}"
    hint = next((text for code, text in _AUTH_HINTS.items() if code == status), None)
    if hint:
        return f"{detail} — {hint}."
    if status == HTTPStatus.NOT_FOUND:
        return f"{detail} — check the server URL and that this endpoint exists on your version."
    return f"{detail}."


def _send(
    method: str,
    url: str,
    service: str,
    *,
    headers: Mapping[str, str],
    params: Mapping[str, Any] | None,
    json: Any | None,  # noqa: ANN401
    timeout: int,
) -> requests.Response:
    """Send one request, translating every failure into a ``ClickException``."""
    try:
        response = requests.request(
            method,
            url,
            headers=dict(headers),
            params=params,
            json=json,
            timeout=timeout,
        )
        response.raise_for_status()
    except requests.Timeout as error:
        message = f"{service} did not respond within {timeout}s: {url}"
        raise click.ClickException(message) from error
    except requests.ConnectionError as error:
        message = f"Could not connect to {service} at {url} — check the server URL and that it is running."
        raise click.ClickException(message) from error
    except requests.HTTPError as error:
        raise click.ClickException(_describe_http_error(error, service)) from error
    except requests.RequestException as error:
        message = f"{service} request failed: {error}"
        raise click.ClickException(message) from error
    return response


def request_json(
    method: str,
    url: str,
    *,
    service: str,
    headers: Mapping[str, str],
    params: Mapping[str, Any] | None = None,
    json: Any | None = None,  # noqa: ANN401
    timeout: int = DEFAULT_TIMEOUT,
) -> Any:  # noqa: ANN401
    """Send a request and decode its JSON body."""
    response = _send(method, url, service, headers=headers, params=params, json=json, timeout=timeout)
    try:
        return orjson.loads(response.content)
    except orjson.JSONDecodeError as error:
        message = f"{service} returned a response that is not valid JSON ({url})."
        raise click.ClickException(message) from error


def request_empty(
    method: str,
    url: str,
    *,
    service: str,
    headers: Mapping[str, str],
    timeout: int = DEFAULT_TIMEOUT,
) -> None:
    """Send a request whose response body is ignored."""
    _send(method, url, service, headers=headers, params=None, json=None, timeout=timeout)
