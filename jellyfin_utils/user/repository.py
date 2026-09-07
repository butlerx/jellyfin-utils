"""Jellyfin reads and writes for user snapshots."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from jellyfin_utils.client import iter_items
from jellyfin_utils.http import request_json
from jellyfin_utils.user.models import CloneCounts, PlaylistSnapshot, UserCloneSnapshot
from jellyfin_utils.user.snapshot import (
    clone_counts,
    copyable_user_data,
    normalise_item_rows,
    playlist_matches,
    user_data_matches,
    verify_user_snapshot,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

SERVICE = "Jellyfin"


def _iter_playlist_items(
    base_url: str,
    headers: dict[str, str],
    playlist_id: str,
    user_id: str,
) -> Iterator[dict]:
    start = 0
    while True:
        payload = request_json(
            "GET",
            f"{base_url}/Playlists/{playlist_id}/Items",
            service=SERVICE,
            headers=headers,
            params={"UserId": user_id, "Limit": 1000, "StartIndex": start},
        )
        if not isinstance(payload, dict):
            return
        page = payload.get("Items") or []
        if not isinstance(page, list) or not page:
            return
        yield from (item for item in page if isinstance(item, dict))
        start += len(page)
        total = payload.get("TotalRecordCount")
        if isinstance(total, int) and start >= total:
            return


def _capture_item_state(
    base_url: str,
    headers: dict[str, str],
    user_id: str,
) -> UserCloneSnapshot:
    return normalise_item_rows(
        iter_items(
            base_url,
            headers,
            {
                "UserId": user_id,
                "Recursive": "true",
                "EnableUserData": "true",
                "EnableImages": "false",
                "ExcludeItemTypes": "Playlist",
                "SortBy": "SortName",
                "SortOrder": "Ascending",
            },
        )
    )


def _capture_playlists(
    base_url: str,
    headers: dict[str, str],
    user_id: str,
) -> tuple[PlaylistSnapshot, ...]:
    playlists = []
    seen_ids: set[str] = set()
    params = {
        "UserId": user_id,
        "Recursive": "true",
        "IncludeItemTypes": "Playlist",
        "EnableUserData": "false",
        "EnableImages": "false",
        "SortBy": "SortName",
        "SortOrder": "Ascending",
    }
    for playlist in iter_items(base_url, headers, params):
        playlist_id = str(playlist.get("Id") or "")
        if not playlist_id or playlist_id in seen_ids:
            continue
        seen_ids.add(playlist_id)
        item_ids = tuple(
            str(item_id)
            for item in _iter_playlist_items(base_url, headers, playlist_id, user_id)
            if (item_id := item.get("Id"))
        )
        playlists.append(
            PlaylistSnapshot(
                name=str(playlist.get("Name") or "Untitled playlist"),
                item_ids=item_ids,
            )
        )
    return tuple(playlists)


def capture_user_snapshot(
    base_url: str,
    headers: dict[str, str],
    user_id: str,
) -> UserCloneSnapshot:
    """Read meaningful item state and visible playlists for one Jellyfin user."""
    item_snapshot = _capture_item_state(base_url, headers, user_id)
    return replace(item_snapshot, playlists=_capture_playlists(base_url, headers, user_id))


def _fetch_destination_user_data(
    base_url: str,
    headers: dict[str, str],
    destination_user_id: str,
    item_id: str,
) -> dict[str, object] | None:
    payload = request_json(
        "GET",
        f"{base_url}/UserItems/{item_id}/UserData",
        service=SERVICE,
        headers=headers,
        params={"userId": destination_user_id},
    )
    state = copyable_user_data({"Id": item_id, "UserData": payload})
    return state[1] if state is not None else None


def resolve_destination_snapshot(
    base_url: str,
    headers: dict[str, str],
    destination_user_id: str,
    expected: UserCloneSnapshot,
    actual: UserCloneSnapshot,
) -> UserCloneSnapshot:
    """Resolve list-query misses through Jellyfin's direct per-item user-data API."""
    preliminary = verify_user_snapshot(expected, actual)
    expected_item_data = dict(expected.item_data)
    actual_item_data = dict(actual.item_data)
    differences = (*preliminary.missing_items, *preliminary.mismatched_items)
    direct_matches = 0
    for difference in differences:
        direct_data = _fetch_destination_user_data(
            base_url,
            headers,
            destination_user_id,
            difference.item_id,
        )
        if direct_data is None:
            actual_item_data.pop(difference.item_id, None)
            continue
        actual_item_data[difference.item_id] = direct_data
        if user_data_matches(expected_item_data[difference.item_id], direct_data):
            direct_matches += 1

    return replace(
        actual,
        item_data=tuple(actual_item_data.items()),
        direct_item_checks=len(differences),
        direct_item_matches=direct_matches,
    )


def _apply_item_data(
    base_url: str,
    headers: dict[str, str],
    destination_user_id: str,
    snapshot: UserCloneSnapshot,
    existing_snapshot: UserCloneSnapshot | None,
) -> tuple[int, int]:
    existing_item_data = dict(existing_snapshot.item_data) if existing_snapshot else {}
    updated = 0
    reused = 0
    for item_id, user_data in snapshot.item_data:
        existing_data = existing_item_data.get(item_id)
        if existing_data is not None and user_data_matches(user_data, existing_data):
            reused += 1
            continue
        request_json(
            "POST",
            f"{base_url}/UserItems/{item_id}/UserData",
            service=SERVICE,
            headers=headers,
            params={"userId": destination_user_id},
            json={field: value for field, value in user_data.items() if field != "PlayedPercentage"},
        )
        updated += 1
    return updated, reused


def _apply_playlists(
    base_url: str,
    headers: dict[str, str],
    destination_user_id: str,
    snapshot: UserCloneSnapshot,
    existing_snapshot: UserCloneSnapshot | None,
) -> tuple[int, int]:
    existing_playlists = existing_snapshot.playlists if existing_snapshot else ()
    created = 0
    reused = 0
    for playlist in snapshot.playlists:
        if any(playlist_matches(playlist, existing) for existing in existing_playlists):
            reused += 1
            continue
        request_json(
            "POST",
            f"{base_url}/Playlists",
            service=SERVICE,
            headers=headers,
            json={
                "Name": playlist.name,
                "Ids": list(playlist.item_ids),
                "UserId": destination_user_id,
                "IsPublic": False,
            },
        )
        created += 1
    return created, reused


def apply_user_snapshot(
    base_url: str,
    headers: dict[str, str],
    destination_user_id: str,
    snapshot: UserCloneSnapshot,
    existing_snapshot: UserCloneSnapshot | None = None,
) -> CloneCounts:
    """Copy missing Jellyfin state while reusing records already cloned correctly."""
    updated, reused = _apply_item_data(
        base_url,
        headers,
        destination_user_id,
        snapshot,
        existing_snapshot,
    )
    created, playlists_reused = _apply_playlists(
        base_url,
        headers,
        destination_user_id,
        snapshot,
        existing_snapshot,
    )
    return clone_counts(
        snapshot,
        user_data_updated=updated,
        user_data_reused=reused,
        playlists_created=created,
        playlists_reused=playlists_reused,
    )
