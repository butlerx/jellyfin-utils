"""Every transport failure must surface as a readable CLI error, not a traceback."""

from __future__ import annotations

import click
import pytest
import requests
import responses

from jellyfin_utils.http import request_empty, request_json

URL = "http://jellyfin.test/System/Info"


@responses.activate
def test_request_json_decodes_body() -> None:
    responses.get(URL, json={"ServerName": "kodi"})
    assert request_json("GET", URL, service="Jellyfin", headers={}) == {"ServerName": "kodi"}


@responses.activate
def test_request_json_sends_headers_and_params() -> None:
    responses.get(URL, json={})
    request_json("GET", URL, service="Jellyfin", headers={"X-Token": "abc"}, params={"Limit": 5})
    request = responses.calls[0].request
    assert request.headers["X-Token"] == "abc"
    assert "Limit=5" in str(request.url)


@responses.activate
def test_unauthorized_explains_the_api_key() -> None:
    responses.get(URL, status=401)
    with pytest.raises(click.ClickException) as caught:
        request_json("GET", URL, service="Jellyfin", headers={})
    assert "401" in caught.value.message
    assert "check the API key" in caught.value.message


@responses.activate
def test_forbidden_explains_missing_permission() -> None:
    responses.get(URL, status=403)
    with pytest.raises(click.ClickException) as caught:
        request_json("GET", URL, service="Jellyfin", headers={})
    assert "lacks permission" in caught.value.message


@responses.activate
def test_not_found_points_at_the_server_url() -> None:
    responses.get(URL, status=404)
    with pytest.raises(click.ClickException) as caught:
        request_json("GET", URL, service="Jellyfin", headers={})
    assert "check the server URL" in caught.value.message


@responses.activate
def test_server_error_names_the_service() -> None:
    responses.get(URL, status=500)
    with pytest.raises(click.ClickException) as caught:
        request_json("GET", URL, service="Jellyseerr", headers={})
    assert caught.value.message.startswith("Jellyseerr returned 500")


@responses.activate
def test_connection_error_names_the_url() -> None:
    responses.get(URL, body=requests.ConnectionError("no route to host"))
    with pytest.raises(click.ClickException) as caught:
        request_json("GET", URL, service="Jellyfin", headers={})
    assert "Could not connect to Jellyfin" in caught.value.message
    assert URL in caught.value.message


@responses.activate
def test_timeout_reports_the_timeout_value() -> None:
    responses.get(URL, body=requests.Timeout("too slow"))
    with pytest.raises(click.ClickException) as caught:
        request_json("GET", URL, service="Jellyfin", headers={}, timeout=15)
    assert "within 15s" in caught.value.message


@responses.activate
def test_non_json_body_is_reported_as_such() -> None:
    responses.get(URL, body="<html>login</html>")
    with pytest.raises(click.ClickException) as caught:
        request_json("GET", URL, service="Jellyfin", headers={})
    assert "not valid JSON" in caught.value.message


@responses.activate
def test_request_empty_ignores_the_body_but_still_checks_status() -> None:
    responses.post(URL, status=204)
    assert request_empty("POST", URL, service="Jellyfin", headers={}) is None

    responses.post(URL, status=500)
    with pytest.raises(click.ClickException):
        request_empty("POST", URL, service="Jellyfin", headers={})
