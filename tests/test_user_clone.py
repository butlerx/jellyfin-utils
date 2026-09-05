"""End-to-end behaviour for cloning and verifying a Jellyfin user."""

from pathlib import Path
from urllib.parse import parse_qs, urlparse

import orjson
import responses
from click.testing import CliRunner
from conftest import BASE_URL, JELLYSEERR_URL, items_page

from jellyfin_utils.cli import cli

RUNNER = CliRunner()


def _clone_args(*extra: str) -> list[str]:
    return [
        "user",
        "clone",
        "source",
        "copy",
        "--email",
        "copy@example.com",
        "--no-password",
        "--server",
        BASE_URL,
        "--token",
        "t",
        "--jellyseerr-server",
        JELLYSEERR_URL,
        "--jellyseerr-token",
        "j",
        *extra,
    ]


def _verify_args(*extra: str) -> list[str]:
    return [
        "user",
        "verify-clone",
        "source",
        "copy",
        "--server",
        BASE_URL,
        "--token",
        "t",
        *extra,
    ]


def _empty_jellyseerr_users() -> dict:
    return {"results": [], "pageInfo": {"results": 0}}


def _watched_item() -> dict:
    return {
        "Id": "watched",
        "Type": "Movie",
        "UserData": {
            "Played": True,
            "PlayedPercentage": 100,
            "PlaybackPositionTicks": 0,
            "PlayCount": 2,
            "IsFavorite": False,
            "Likes": None,
            "LastPlayedDate": "2026-08-01T12:00:00Z",
        },
    }


def _favorite_item() -> dict:
    return {
        "Id": "favorite",
        "Type": "Movie",
        "UserData": {
            "Played": False,
            "PlaybackPositionTicks": 0,
            "PlayCount": 0,
            "IsFavorite": True,
        },
    }


def _register_snapshot(
    user_data_items: list[dict],
    *,
    playlist_id: str | None = None,
    playlist_name: str = "Road Trip",
    playlist_items: tuple[str, ...] = ("song-1", "song-2"),
) -> None:
    responses.get(
        f"{BASE_URL}/Items",
        json=items_page(user_data_items, total=len(user_data_items)),
    )
    playlists = (
        [{"Id": playlist_id, "Name": playlist_name, "Type": "Playlist"}] if playlist_id is not None else []
    )
    responses.get(f"{BASE_URL}/Items", json=items_page(playlists, total=len(playlists)))
    if playlist_id is not None:
        responses.get(
            f"{BASE_URL}/Playlists/{playlist_id}/Items",
            json=items_page([{"Id": item_id} for item_id in playlist_items], total=len(playlist_items)),
        )


def _register_user_management_preflight() -> None:
    responses.get(
        f"{JELLYSEERR_URL}/api/v1/auth/me",
        json={"id": 1, "username": "owner", "permissions": 2},
    )


@responses.activate
def test_user_clone_copies_and_verifies_watch_state_favorites_playlists_and_email() -> None:
    responses.get(f"{BASE_URL}/Users", json=[{"Id": "source-id", "Name": "Source"}])
    _register_user_management_preflight()
    responses.get(f"{JELLYSEERR_URL}/api/v1/user", json=_empty_jellyseerr_users())
    _register_snapshot([_watched_item(), _favorite_item()], playlist_id="playlist-1")
    responses.post(f"{BASE_URL}/Users/New", json={"Id": "copy-id", "Name": "copy"})
    responses.post(f"{BASE_URL}/UserItems/watched/UserData", json={})
    responses.post(f"{BASE_URL}/UserItems/favorite/UserData", json={})
    responses.post(f"{BASE_URL}/Playlists", json={"Id": "playlist-copy"})
    _register_snapshot([_watched_item(), _favorite_item()], playlist_id="playlist-copy")
    responses.post(
        f"{JELLYSEERR_URL}/api/v1/user/import-from-jellyfin",
        json=[{"id": 9, "jellyfinUserId": "copy-id"}],
    )
    responses.get(
        f"{JELLYSEERR_URL}/api/v1/user/9/settings/main",
        json={"username": None, "email": "copy"},
    )
    responses.post(
        f"{JELLYSEERR_URL}/api/v1/user/9/settings/main",
        json={"username": "copy", "email": "copy@example.com"},
    )

    result = RUNNER.invoke(cli, [*_clone_args(), "--output", "json"])

    assert result.exit_code == 0, result.output
    assert orjson.loads(result.output) == {
        "created": True,
        "resumed": False,
        "source_username": "Source",
        "username": "copy",
        "email": "copy@example.com",
        "jellyfin_user_id": "copy-id",
        "jellyseerr_user_id": 9,
        "watch_items_copied": 1,
        "favorites_copied": 1,
        "playlists_copied": 1,
        "playlist_items_copied": 2,
        "user_data_updated": 2,
        "user_data_reused": 0,
        "playlists_created": 1,
        "playlists_reused": 0,
        "jellyseerr_requests_copied": 0,
        "password_set": False,
        "verified": True,
        "user_data_expected": 2,
        "user_data_matched": 2,
        "watch_items_expected": 1,
        "watch_items_matched": 1,
        "favorites_expected": 1,
        "favorites_matched": 1,
        "playlists_expected": 1,
        "playlists_matched": 1,
        "direct_item_checks": 0,
        "direct_item_matches": 0,
        "source_duplicate_rows_removed": 0,
        "source_duplicate_item_count": 0,
        "source_duplicate_item_ids": [],
        "missing_item_count": 0,
        "missing_item_ids": [],
        "missing_items": [],
        "mismatched_item_count": 0,
        "mismatched_item_ids": [],
        "mismatched_items": [],
        "missing_playlists": [],
        "details_truncated": False,
        "message": 'Cloned Jellyfin user "Source" to "copy".',
    }

    user_data_calls = [
        call
        for call in responses.calls
        if call.request.method == "POST" and "/UserItems/" in (call.request.url or "")
    ]
    assert [urlparse(call.request.url or "").path for call in user_data_calls] == [
        "/UserItems/watched/UserData",
        "/UserItems/favorite/UserData",
    ]
    assert all(
        parse_qs(urlparse(call.request.url or "").query) == {"userId": ["copy-id"]}
        for call in user_data_calls
    )
    watched_body = user_data_calls[0].request.body
    assert watched_body is not None
    expected_watched_body = _watched_item()["UserData"].copy()
    expected_watched_body.pop("PlayedPercentage")
    assert orjson.loads(watched_body) == expected_watched_body

    playlist_call = next(
        call
        for call in responses.calls
        if call.request.method == "POST" and urlparse(call.request.url or "").path == "/Playlists"
    )
    playlist_body = playlist_call.request.body
    assert playlist_body is not None
    assert orjson.loads(playlist_body) == {
        "Name": "Road Trip",
        "Ids": ["song-1", "song-2"],
        "UserId": "copy-id",
        "IsPublic": False,
    }


@responses.activate
def test_user_clone_resumes_an_existing_jellyfin_user_without_rewriting_matching_data() -> None:
    responses.get(
        f"{BASE_URL}/Users",
        json=[{"Id": "source-id", "Name": "source"}, {"Id": "copy-id", "Name": "copy"}],
    )
    _register_user_management_preflight()
    responses.get(f"{JELLYSEERR_URL}/api/v1/user", json=_empty_jellyseerr_users())
    _register_snapshot([_watched_item(), _favorite_item()], playlist_id="source-playlist")
    _register_snapshot([_watched_item(), _favorite_item()], playlist_id="copy-playlist")
    _register_snapshot([_watched_item(), _favorite_item()], playlist_id="copy-playlist")
    responses.post(
        f"{JELLYSEERR_URL}/api/v1/user/import-from-jellyfin",
        json=[{"id": 9, "jellyfinUserId": "copy-id"}],
    )
    responses.get(f"{JELLYSEERR_URL}/api/v1/user/9/settings/main", json={"email": "copy"})
    responses.post(
        f"{JELLYSEERR_URL}/api/v1/user/9/settings/main",
        json={"username": "copy", "email": "copy@example.com"},
    )
    args = _clone_args()
    args.remove("--no-password")

    result = RUNNER.invoke(cli, [*args, "--output", "json"])

    assert result.exit_code == 0, result.output
    payload = orjson.loads(result.output)
    assert payload["created"] is False
    assert payload["resumed"] is True
    assert payload["verified"] is True
    assert payload["user_data_updated"] == 0
    assert payload["user_data_reused"] == 2
    assert payload["playlists_created"] == 0
    assert payload["playlists_reused"] == 1
    assert payload["password_set"] is None
    assert "Password" not in result.output
    jellyfin_writes = [
        call
        for call in responses.calls
        if call.request.method == "POST"
        and urlparse(call.request.url or "").netloc == urlparse(BASE_URL).netloc
    ]
    assert jellyfin_writes == []


@responses.activate
def test_user_clone_repairs_only_missing_state_when_resumed() -> None:
    responses.get(
        f"{BASE_URL}/Users",
        json=[{"Id": "source-id", "Name": "source"}, {"Id": "copy-id", "Name": "copy"}],
    )
    _register_user_management_preflight()
    responses.get(f"{JELLYSEERR_URL}/api/v1/user", json=_empty_jellyseerr_users())
    _register_snapshot([_watched_item(), _favorite_item()], playlist_id="source-playlist")
    _register_snapshot([_watched_item()], playlist_id=None)
    responses.get(f"{BASE_URL}/UserItems/favorite/UserData", json={"IsFavorite": False})
    responses.post(f"{BASE_URL}/UserItems/favorite/UserData", json={})
    responses.post(f"{BASE_URL}/Playlists", json={"Id": "copy-playlist"})
    _register_snapshot([_watched_item(), _favorite_item()], playlist_id="copy-playlist")
    responses.post(
        f"{JELLYSEERR_URL}/api/v1/user/import-from-jellyfin",
        json=[{"id": 9, "jellyfinUserId": "copy-id"}],
    )
    responses.get(f"{JELLYSEERR_URL}/api/v1/user/9/settings/main", json={"email": "copy"})
    responses.post(
        f"{JELLYSEERR_URL}/api/v1/user/9/settings/main",
        json={"username": "copy", "email": "copy@example.com"},
    )

    result = RUNNER.invoke(cli, [*_clone_args(), "--output", "json"])

    assert result.exit_code == 0, result.output
    payload = orjson.loads(result.output)
    assert payload["verified"] is True
    assert payload["user_data_updated"] == 1
    assert payload["user_data_reused"] == 1
    assert payload["playlists_created"] == 1
    assert payload["playlists_reused"] == 0
    jellyfin_write_paths = [
        urlparse(call.request.url or "").path
        for call in responses.calls
        if call.request.method == "POST"
        and urlparse(call.request.url or "").netloc == urlparse(BASE_URL).netloc
    ]
    assert jellyfin_write_paths == ["/UserItems/favorite/UserData", "/Playlists"]


@responses.activate
def test_user_clone_reuses_an_existing_linked_jellyseerr_account() -> None:
    linked_user = {
        "id": 9,
        "email": "copy@example.com",
        "username": None,
        "jellyfinUsername": "copy",
        "jellyfinUserId": "copy-id",
    }
    responses.get(
        f"{BASE_URL}/Users",
        json=[{"Id": "source-id", "Name": "source"}, {"Id": "copy-id", "Name": "copy"}],
    )
    _register_user_management_preflight()
    responses.get(
        f"{JELLYSEERR_URL}/api/v1/user",
        json={"results": [linked_user], "pageInfo": {"results": 1}},
    )
    _register_snapshot([], playlist_id=None)
    _register_snapshot([], playlist_id=None)
    _register_snapshot([], playlist_id=None)

    result = RUNNER.invoke(cli, [*_clone_args(), "--output", "json"])

    assert result.exit_code == 0, result.output
    assert orjson.loads(result.output)["resumed"] is True
    assert not any("import-from-jellyfin" in (call.request.url or "") for call in responses.calls)
    assert not any("/settings/main" in (call.request.url or "") for call in responses.calls)


@responses.activate
def test_user_clone_checks_jellyseerr_permissions_before_writing_jellyfin() -> None:
    responses.get(f"{BASE_URL}/Users", json=[{"Id": "source-id", "Name": "source"}])
    responses.get(
        f"{JELLYSEERR_URL}/api/v1/auth/me",
        json={"id": 4, "username": "limited", "permissions": 32},
    )

    result = RUNNER.invoke(cli, _clone_args())

    assert result.exit_code == 1
    assert 'authenticates as "limited"' in result.output
    assert "lacks Admin or Manage Users permission" in result.output
    assert not any(call.request.method == "POST" for call in responses.calls)
    assert not any(urlparse(call.request.url or "").path == "/Items" for call in responses.calls)


@responses.activate
def test_verify_clone_reports_missing_watch_data_favorites_and_playlists() -> None:
    responses.get(
        f"{BASE_URL}/Users",
        json=[{"Id": "source-id", "Name": "source"}, {"Id": "copy-id", "Name": "copy"}],
    )
    _register_snapshot([_watched_item(), _favorite_item()], playlist_id="source-playlist")
    _register_snapshot([_watched_item()], playlist_id=None)
    responses.get(f"{BASE_URL}/UserItems/favorite/UserData", json={"IsFavorite": False})

    result = RUNNER.invoke(cli, [*_verify_args(), "--output", "json"])

    assert result.exit_code == 0, result.output
    assert orjson.loads(result.output) == {
        "source_username": "source",
        "destination_username": "copy",
        "verified": False,
        "user_data_expected": 2,
        "user_data_matched": 1,
        "watch_items_expected": 1,
        "watch_items_matched": 1,
        "favorites_expected": 1,
        "favorites_matched": 0,
        "playlists_expected": 1,
        "playlists_matched": 0,
        "direct_item_checks": 1,
        "direct_item_matches": 0,
        "source_duplicate_rows_removed": 0,
        "source_duplicate_item_count": 0,
        "source_duplicate_item_ids": [],
        "missing_item_count": 1,
        "missing_item_ids": ["favorite"],
        "missing_items": [
            {
                "id": "favorite",
                "name": "favorite",
                "type": "Movie",
                "fields": [],
                "expected": {
                    "Played": False,
                    "PlaybackPositionTicks": 0,
                    "PlayCount": 0,
                    "IsFavorite": True,
                },
                "actual": {},
            }
        ],
        "mismatched_item_count": 0,
        "mismatched_item_ids": [],
        "mismatched_items": [],
        "missing_playlists": ["Road Trip"],
        "details_truncated": False,
        "details_file": None,
        "message": 'Clone differs: "source" → "copy".',
    }


@responses.activate
def test_verify_clone_deduplicates_source_rows() -> None:
    responses.get(
        f"{BASE_URL}/Users",
        json=[{"Id": "source-id", "Name": "source"}, {"Id": "copy-id", "Name": "copy"}],
    )
    watched = _watched_item()
    _register_snapshot([watched, watched], playlist_id=None)
    _register_snapshot([watched], playlist_id=None)

    result = RUNNER.invoke(cli, [*_verify_args(), "--output", "json"])

    assert result.exit_code == 0, result.output
    payload = orjson.loads(result.output)
    assert payload["verified"] is True
    assert payload["user_data_expected"] == 1
    assert payload["source_duplicate_rows_removed"] == 1
    assert payload["source_duplicate_item_ids"] == ["watched"]


@responses.activate
def test_verify_clone_ignores_derived_folder_play_state_but_keeps_favorites() -> None:
    responses.get(
        f"{BASE_URL}/Users",
        json=[{"Id": "source-id", "Name": "source"}, {"Id": "copy-id", "Name": "copy"}],
    )
    derived_collection = {
        "Id": "derived-collection",
        "Name": "Derived Collection",
        "Type": "BoxSet",
        "IsFolder": True,
        "UserData": {
            "PlaybackPositionTicks": 0,
            "PlayCount": 0,
            "IsFavorite": False,
            "Played": True,
        },
    }
    favorite_collection = {
        "Id": "favorite-collection",
        "Name": "Favorite Collection",
        "Type": "BoxSet",
        "IsFolder": True,
        "UserData": {
            "PlaybackPositionTicks": 0,
            "PlayCount": 0,
            "IsFavorite": True,
            "Played": True,
        },
    }
    destination_favorite = {
        **favorite_collection,
        "UserData": {**favorite_collection["UserData"], "Played": False},
    }
    _register_snapshot([derived_collection, favorite_collection], playlist_id=None)
    _register_snapshot([destination_favorite], playlist_id=None)

    result = RUNNER.invoke(cli, [*_verify_args(), "--output", "json"])

    assert result.exit_code == 0, result.output
    payload = orjson.loads(result.output)
    assert payload["verified"] is True
    assert payload["user_data_expected"] == 1
    assert payload["watch_items_expected"] == 0
    assert payload["favorites_expected"] == 1


@responses.activate
def test_verify_clone_resolves_destination_items_hidden_from_the_items_query() -> None:
    responses.get(
        f"{BASE_URL}/Users",
        json=[{"Id": "source-id", "Name": "source"}, {"Id": "copy-id", "Name": "copy"}],
    )
    favorite = _favorite_item()
    _register_snapshot([favorite], playlist_id=None)
    _register_snapshot([], playlist_id=None)
    responses.get(f"{BASE_URL}/UserItems/favorite/UserData", json=favorite["UserData"])

    result = RUNNER.invoke(cli, [*_verify_args(), "--output", "json"])

    assert result.exit_code == 0, result.output
    payload = orjson.loads(result.output)
    assert payload["verified"] is True
    assert payload["user_data_matched"] == 1
    assert payload["direct_item_checks"] == 1
    assert payload["direct_item_matches"] == 1


@responses.activate
def test_verify_clone_reports_names_values_and_writes_full_details(tmp_path: Path) -> None:
    responses.get(
        f"{BASE_URL}/Users",
        json=[{"Id": "source-id", "Name": "source"}, {"Id": "copy-id", "Name": "copy"}],
    )
    expected = _watched_item()
    expected["Name"] = "Example Movie"
    actual = _watched_item()
    actual["Name"] = "Example Movie"
    actual["UserData"] = {**actual["UserData"], "PlayCount": 1}
    _register_snapshot([expected], playlist_id=None)
    _register_snapshot([actual], playlist_id=None)
    responses.get(f"{BASE_URL}/UserItems/watched/UserData", json=actual["UserData"])
    details_file = tmp_path / "clone-differences.json"

    result = RUNNER.invoke(cli, _verify_args("--details-file", str(details_file)))

    assert result.exit_code == 0, result.output
    assert "Example Movie" in result.output
    assert "PlayCount: 2 → 1" in result.output
    details = orjson.loads(details_file.read_bytes())
    mismatch = details["mismatched_items"][0]
    assert mismatch["fields"] == ["PlayCount"]
    assert mismatch["expected"] == {"PlayCount": 2}
    assert mismatch["actual"] == {"PlayCount": 1}


@responses.activate
def test_user_clone_failure_reports_persistent_field_differences() -> None:
    responses.get(
        f"{BASE_URL}/Users",
        json=[{"Id": "source-id", "Name": "source"}, {"Id": "copy-id", "Name": "copy"}],
    )
    _register_user_management_preflight()
    responses.get(f"{JELLYSEERR_URL}/api/v1/user", json=_empty_jellyseerr_users())
    expected = _watched_item()
    expected["Name"] = "Example Movie"
    actual = _watched_item()
    actual["Name"] = "Example Movie"
    actual["UserData"] = {**actual["UserData"], "PlayCount": 1}
    _register_snapshot([expected], playlist_id=None)
    _register_snapshot([actual], playlist_id=None)
    responses.get(f"{BASE_URL}/UserItems/watched/UserData", json=actual["UserData"])
    responses.post(f"{BASE_URL}/UserItems/watched/UserData", json={})
    _register_snapshot([actual], playlist_id=None)
    responses.get(f"{BASE_URL}/UserItems/watched/UserData", json=actual["UserData"])

    result = RUNNER.invoke(cli, _clone_args())

    assert result.exit_code == 1
    assert "Example Movie" in result.output
    assert "PlayCount: 2 → 1" in result.output
    assert "repeating the unchanged clone is unlikely to help" in result.output
    assert "Re-run the same command" not in result.output


@responses.activate
def test_user_clone_rejects_a_missing_source_before_creating_anything() -> None:
    responses.get(f"{BASE_URL}/Users", json=[])

    result = RUNNER.invoke(cli, _clone_args())

    assert result.exit_code == 2
    assert 'Jellyfin user "source" does not exist' in result.output
    assert not any(call.request.method == "POST" for call in responses.calls)


@responses.activate
def test_user_clone_rejects_an_existing_unrelated_jellyseerr_email_before_creating_anything() -> None:
    responses.get(f"{BASE_URL}/Users", json=[{"Id": "source-id", "Name": "source"}])
    _register_user_management_preflight()
    responses.get(
        f"{JELLYSEERR_URL}/api/v1/user",
        json={
            "results": [{"id": 4, "email": "COPY@example.com"}],
            "pageInfo": {"results": 1},
        },
    )

    result = RUNNER.invoke(cli, _clone_args())

    assert result.exit_code == 2
    assert "already exists" in result.output
    assert not any(call.request.method == "POST" for call in responses.calls)


def test_user_clone_rejects_the_same_source_and_destination_without_network_calls() -> None:
    args = _clone_args()
    args[3] = "SOURCE"

    result = RUNNER.invoke(cli, args)

    assert result.exit_code == 2
    assert "must be different" in result.output
