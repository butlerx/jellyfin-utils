"""Jellyseerr client: paging, permission checks, and profile updates against a fake API."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

import click
import orjson
import pytest
import responses
from conftest import JELLYSEERR_URL

from jellyfin_utils import jellyseerr

if TYPE_CHECKING:
    from requests import PreparedRequest

KEY = "seerr-key"


def _sent_json(call_index: int) -> object:
    """Decode the JSON body the client sent on one call."""
    body = responses.calls[call_index].request.body
    assert isinstance(body, bytes)
    return orjson.loads(body)


def _paged(url: str, results: list, *, page_size: int, total: int | None = None) -> None:
    """Serve ``results`` the way Jellyseerr pages them, using the ``take``/``skip`` params."""

    def callback(request: PreparedRequest) -> tuple[int, dict, str]:
        params = parse_qs(urlsplit(str(request.url)).query)
        skip, take = int(params["skip"][0]), int(params["take"][0])
        page = results[skip : skip + min(take, page_size)]
        body = {"results": page, "pageInfo": {"results": total if total is not None else len(results)}}
        return 200, {}, orjson.dumps(body).decode()

    responses.add_callback(responses.GET, url, callback=callback, content_type="application/json")


@responses.activate
def test_get_requests_pages_until_every_request_is_seen() -> None:
    requests_data = [
        {"id": i, "status": 2, "requestedBy": {"username": f"user{i}"}, "media": {"tmdbId": 100 + i}}
        for i in range(5)
    ]
    _paged(f"{JELLYSEERR_URL}/api/v1/request", requests_data, page_size=2)

    result = jellyseerr.get_requests(JELLYSEERR_URL, KEY)

    assert [entry["id"] for entry in result] == [0, 1, 2, 3, 4]
    assert result[0] == {
        "id": 0,
        "status": 2,
        "requested_by": "user0",
        "tmdb_id": 100,
        "media_type": None,
    }
    assert responses.calls[0].request.headers["X-Api-Key"] == KEY


@responses.activate
def test_requests_stop_on_an_empty_or_unexpected_page() -> None:
    responses.get(f"{JELLYSEERR_URL}/api/v1/request", json=["not", "a", "page"])
    assert jellyseerr.get_requests(JELLYSEERR_URL, KEY) == []


@responses.activate
def test_requesters_are_grouped_per_tmdb_id_and_skip_bad_rows() -> None:
    requests_data = [
        {"id": 1, "requestedBy": {"username": "bob"}, "media": {"tmdbId": 550}},
        {"id": 2, "requestedBy": {"username": "alice"}, "media": {"tmdbId": "550"}},
        {"id": 3, "requestedBy": {"username": "alice"}, "media": {"tmdbId": 550}},
        {"id": 4, "requestedBy": {}, "media": {"tmdbId": 9}},
        {"id": 5, "requestedBy": {"username": "carol"}, "media": {"tmdbId": "not-a-number"}},
        {"id": 6, "requestedBy": {"username": "dave"}, "media": {}},
    ]
    _paged(f"{JELLYSEERR_URL}/api/v1/request", requests_data, page_size=10)

    assert jellyseerr.get_requesters_by_tmdb_id(JELLYSEERR_URL, KEY) == {550: ("alice", "bob")}


@responses.activate
def test_requester_name_falls_back_when_username_is_null() -> None:
    requests_data = [
        {"id": 1, "requestedBy": {"username": None, "jellyfinUsername": "isobel"}, "media": {"tmdbId": 1}},
        {"id": 2, "requestedBy": {"username": "local", "jellyfinUsername": None}, "media": {"tmdbId": 2}},
        {"id": 3, "requestedBy": {"username": "local", "jellyfinUsername": "linked"}, "media": {"tmdbId": 3}},
        {"id": 4, "requestedBy": {"username": None, "displayName": "Shown"}, "media": {"tmdbId": 4}},
    ]
    _paged(f"{JELLYSEERR_URL}/api/v1/request", requests_data, page_size=10)

    assert [entry["requested_by"] for entry in jellyseerr.get_requests(JELLYSEERR_URL, KEY)] == [
        "isobel",
        "local",
        "linked",
        "Shown",
    ]
    assert jellyseerr.get_requesters_by_tmdb_id(JELLYSEERR_URL, KEY) == {
        1: ("isobel",),
        2: ("local",),
        3: ("linked",),
        4: ("Shown",),
    }


@responses.activate
def test_user_search_pages_and_ignores_non_dict_rows() -> None:
    users = [{"id": 1, "email": "a@example.com"}, "junk", {"id": 2, "email": "b@example.com"}]
    _paged(f"{JELLYSEERR_URL}/api/v1/user", users, page_size=2)

    found = jellyseerr.get_jellyseerr_users(JELLYSEERR_URL, KEY, "example")

    assert [user["id"] for user in found] == [1, 2]
    assert parse_qs(urlsplit(str(responses.calls[0].request.url)).query)["q"] == ["example"]


@responses.activate
def test_user_search_treats_a_missing_total_as_the_end() -> None:
    responses.get(
        f"{JELLYSEERR_URL}/api/v1/user", json={"results": [{"id": 7}], "pageInfo": {"results": "?"}}
    )
    assert jellyseerr.get_jellyseerr_users(JELLYSEERR_URL, KEY, "x") == [{"id": 7}]
    assert len(responses.calls) == 1


@responses.activate
def test_access_check_passes_for_admin_permission() -> None:
    responses.get(f"{JELLYSEERR_URL}/api/v1/auth/me", json={"permissions": 2})  # Jellyseerr ADMIN bit
    jellyseerr.check_jellyseerr_user_management_access(JELLYSEERR_URL, KEY)


@responses.activate
def test_access_check_passes_for_manage_users_permission() -> None:
    responses.get(
        f"{JELLYSEERR_URL}/api/v1/auth/me",
        json={"permissions": 8, "username": "ops"},  # MANAGE_USERS bit
    )
    jellyseerr.check_jellyseerr_user_management_access(JELLYSEERR_URL, KEY)


@responses.activate
def test_access_check_names_the_account_that_lacks_permission() -> None:
    responses.get(f"{JELLYSEERR_URL}/api/v1/auth/me", json={"permissions": 1, "email": "me@example.com"})
    with pytest.raises(click.ClickException) as caught:
        jellyseerr.check_jellyseerr_user_management_access(JELLYSEERR_URL, KEY)
    assert 'authenticates as "me@example.com"' in caught.value.message
    assert "lacks Admin or Manage Users permission" in caught.value.message


@responses.activate
def test_access_check_falls_back_to_id_then_unknown_user() -> None:
    responses.get(f"{JELLYSEERR_URL}/api/v1/auth/me", json={"permissions": 0, "id": 42})
    with pytest.raises(click.ClickException, match='authenticates as "42"'):
        jellyseerr.check_jellyseerr_user_management_access(JELLYSEERR_URL, KEY)

    responses.replace(responses.GET, f"{JELLYSEERR_URL}/api/v1/auth/me", json={"permissions": "x"})
    with pytest.raises(click.ClickException, match='authenticates as "unknown user"'):
        jellyseerr.check_jellyseerr_user_management_access(JELLYSEERR_URL, KEY)


@responses.activate
def test_access_check_rejects_a_non_object_profile() -> None:
    responses.get(f"{JELLYSEERR_URL}/api/v1/auth/me", json=[])
    with pytest.raises(click.ClickException, match="invalid current-user response"):
        jellyseerr.check_jellyseerr_user_management_access(JELLYSEERR_URL, KEY)


@responses.activate
def test_find_linked_user_matches_on_the_jellyfin_id() -> None:
    _paged(
        f"{JELLYSEERR_URL}/api/v1/user",
        [
            {"id": 1, "jellyfinUserId": "other"},
            {"id": 2, "jellyfinUserId": "jf-1"},
        ],
        page_size=10,
    )
    assert jellyseerr.find_linked_jellyseerr_user(JELLYSEERR_URL, KEY, "jf-1", "bob") == {
        "id": 2,
        "jellyfinUserId": "jf-1",
    }
    assert jellyseerr.find_linked_jellyseerr_user(JELLYSEERR_URL, KEY, "missing", "bob") is None


@responses.activate
def test_import_prefers_the_matching_user_and_falls_back_to_the_first() -> None:
    responses.post(
        f"{JELLYSEERR_URL}/api/v1/user/import-from-jellyfin",
        json=[{"id": 1, "jellyfinUserId": "x"}, "junk", {"id": 2, "jellyfinUserId": "jf-9"}],
    )
    assert jellyseerr.import_jellyfin_user(JELLYSEERR_URL, KEY, "jf-9") == {"id": 2, "jellyfinUserId": "jf-9"}
    body = _sent_json(0)
    assert body == {"jellyfinUserIds": ["jf-9"]}

    responses.replace(
        responses.POST,
        f"{JELLYSEERR_URL}/api/v1/user/import-from-jellyfin",
        json=[{"id": 1, "jellyfinUserId": "x"}],
    )
    assert jellyseerr.import_jellyfin_user(JELLYSEERR_URL, KEY, "jf-9") == {"id": 1, "jellyfinUserId": "x"}


@responses.activate
def test_import_returns_none_when_the_response_is_not_a_list() -> None:
    responses.post(f"{JELLYSEERR_URL}/api/v1/user/import-from-jellyfin", json={"error": "nope"})
    assert jellyseerr.import_jellyfin_user(JELLYSEERR_URL, KEY, "jf-9") is None


@responses.activate
def test_profile_update_merges_into_the_current_settings() -> None:
    url = f"{JELLYSEERR_URL}/api/v1/user/7/settings/main"
    responses.get(url, json={"movieQuotaLimit": 5, "username": "old"})
    responses.post(url, json={"username": "copy", "email": "copy@example.com", "movieQuotaLimit": 5})

    result = jellyseerr.update_jellyseerr_profile(JELLYSEERR_URL, KEY, 7, "copy", "copy@example.com")

    assert result["username"] == "copy"
    sent = _sent_json(1)
    assert sent == {"movieQuotaLimit": 5, "username": "copy", "email": "copy@example.com"}


@responses.activate
def test_profile_update_falls_back_to_the_submitted_body() -> None:
    url = f"{JELLYSEERR_URL}/api/v1/user/8/settings/main"
    responses.get(url, body="null", content_type="application/json")
    responses.post(url, json=["unexpected"])

    result = jellyseerr.update_jellyseerr_profile(JELLYSEERR_URL, KEY, 8, "copy", "copy@example.com")

    assert result == {"username": "copy", "email": "copy@example.com"}
