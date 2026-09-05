"""Capture and apply the Jellyfin data that belongs to a user."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import TYPE_CHECKING

from jellyfin_utils.client import iter_items
from jellyfin_utils.http import request_json

if TYPE_CHECKING:
    from collections.abc import Iterator

SERVICE = "Jellyfin"

_COPYABLE_USER_DATA_FIELDS = (
    "Rating",
    "PlayedPercentage",
    "PlaybackPositionTicks",
    "PlayCount",
    "IsFavorite",
    "Likes",
    "LastPlayedDate",
    "Played",
)
_USER_DATA_DEFAULTS: dict[str, object] = {
    "Rating": None,
    "PlayedPercentage": None,
    "PlaybackPositionTicks": 0,
    "PlayCount": 0,
    "IsFavorite": False,
    "Likes": None,
    "LastPlayedDate": None,
    "Played": False,
}
_FOLDER_ITEM_TYPES = frozenset(
    {
        "BoxSet",
        "CollectionFolder",
        "Folder",
        "PhotoAlbum",
        "Playlist",
        "Season",
        "Series",
        "UserView",
    }
)
_DERIVED_FOLDER_PLAY_STATE_FIELDS = frozenset(
    {
        "PlayedPercentage",
        "PlaybackPositionTicks",
        "PlayCount",
        "LastPlayedDate",
        "Played",
    }
)


@dataclass(frozen=True)
class PlaylistSnapshot:
    """A playlist name and its ordered Jellyfin item IDs."""

    name: str
    item_ids: tuple[str, ...]


@dataclass(frozen=True)
class UserCloneSnapshot:
    """The per-item state and playlists to copy to a new user."""

    item_data: tuple[tuple[str, dict[str, object]], ...]
    playlists: tuple[PlaylistSnapshot, ...]
    item_descriptions: tuple[tuple[str, str, str], ...] = ()
    duplicate_item_ids: tuple[str, ...] = ()
    duplicate_rows_removed: int = 0
    direct_item_checks: int = 0
    direct_item_matches: int = 0


@dataclass(frozen=True)
class CloneCounts:
    """Counts describing the Jellyfin records copied or reused for a user."""

    watch_items: int
    favorites: int
    playlists: int
    playlist_items: int
    user_data_updated: int
    user_data_reused: int
    playlists_created: int
    playlists_reused: int


@dataclass(frozen=True)
class ItemDifference:
    """One missing or mismatched destination user-data record."""

    item_id: str
    name: str
    item_type: str
    fields: tuple[str, ...] = ()
    expected: tuple[tuple[str, object], ...] = ()
    actual: tuple[tuple[str, object], ...] = ()


@dataclass(frozen=True)
class VerificationResult:
    """A comparison of source state against a destination user."""

    user_data_expected: int
    user_data_matched: int
    watch_items_expected: int
    watch_items_matched: int
    favorites_expected: int
    favorites_matched: int
    playlists_expected: int
    playlists_matched: int
    direct_item_checks: int
    direct_item_matches: int
    missing_items: tuple[ItemDifference, ...]
    mismatched_items: tuple[ItemDifference, ...]
    missing_playlists: tuple[str, ...]
    duplicate_source_item_ids: tuple[str, ...]
    duplicate_source_rows_removed: int

    @property
    def missing_item_ids(self) -> tuple[str, ...]:
        """Return IDs absent from the destination's resolved user data."""
        return tuple(item.item_id for item in self.missing_items)

    @property
    def mismatched_item_ids(self) -> tuple[str, ...]:
        """Return IDs whose destination user-data fields differ."""
        return tuple(item.item_id for item in self.mismatched_items)

    @property
    def verified(self) -> bool:
        """Return whether every source record has an exact destination match."""
        return not (self.missing_items or self.mismatched_items or self.missing_playlists)


def _positive_number(value: object) -> bool:
    return isinstance(value, int | float) and value > 0


def _has_meaningful_user_data(data: dict[str, object]) -> bool:
    return (
        bool(data.get("Played"))
        or _positive_number(data.get("PlayedPercentage"))
        or _positive_number(data.get("PlaybackPositionTicks"))
        or _positive_number(data.get("PlayCount"))
        or bool(data.get("IsFavorite"))
        or data.get("Likes") is not None
        or data.get("Rating") is not None
        or bool(data.get("LastPlayedDate"))
    )


def _has_watch_data(data: dict[str, object]) -> bool:
    return (
        bool(data.get("Played"))
        or _positive_number(data.get("PlayedPercentage"))
        or _positive_number(data.get("PlaybackPositionTicks"))
        or _positive_number(data.get("PlayCount"))
        or bool(data.get("LastPlayedDate"))
    )


def _copyable_user_data(item: dict) -> tuple[str, dict[str, object]] | None:
    item_id = item.get("Id")
    raw_data = item.get("UserData")
    if not item_id or not isinstance(raw_data, dict):
        return None

    data = {field: raw_data[field] for field in _COPYABLE_USER_DATA_FIELDS if field in raw_data}
    if bool(item.get("IsFolder")) or item.get("Type") in _FOLDER_ITEM_TYPES:
        data = {
            field: value for field, value in data.items() if field not in _DERIVED_FOLDER_PLAY_STATE_FIELDS
        }
    if not _has_meaningful_user_data(data):
        return None
    return str(item_id), data


def _normalise_user_data_value(field: str, value: object) -> object:
    if field != "LastPlayedDate" or not isinstance(value, str):
        return value
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return value


def _mismatched_user_data_fields(
    expected: dict[str, object],
    actual: dict[str, object],
) -> tuple[str, ...]:
    return tuple(
        field
        for field, default in _USER_DATA_DEFAULTS.items()
        if _normalise_user_data_value(field, expected.get(field, default))
        != _normalise_user_data_value(field, actual.get(field, default))
    )


def _user_data_matches(expected: dict[str, object], actual: dict[str, object]) -> bool:
    return not _mismatched_user_data_fields(expected, actual)


def _playlist_matches(expected: PlaylistSnapshot, actual: PlaylistSnapshot) -> bool:
    return expected.name.casefold() == actual.name.casefold() and expected.item_ids == actual.item_ids


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
        for item in page:
            if isinstance(item, dict):
                yield item
        start += len(page)
        total = payload.get("TotalRecordCount")
        if isinstance(total, int) and start >= total:
            return


def capture_user_snapshot(
    base_url: str,
    headers: dict[str, str],
    user_id: str,
) -> UserCloneSnapshot:
    """Read and deduplicate meaningful item state and visible playlists for one user."""
    item_data: dict[str, dict[str, object]] = {}
    item_descriptions: dict[str, tuple[str, str]] = {}
    duplicate_item_ids: set[str] = set()
    duplicate_rows_removed = 0
    for item in iter_items(
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
    ):
        state = _copyable_user_data(item)
        if state is None:
            continue
        item_id, user_data = state
        if item_id in item_data:
            duplicate_item_ids.add(item_id)
            duplicate_rows_removed += 1
        item_data[item_id] = user_data
        item_descriptions.setdefault(
            item_id,
            (
                str(item.get("Name") or item_id),
                str(item.get("Type") or "Unknown"),
            ),
        )

    playlists = []
    seen_playlist_ids: set[str] = set()
    for playlist in iter_items(
        base_url,
        headers,
        {
            "UserId": user_id,
            "Recursive": "true",
            "IncludeItemTypes": "Playlist",
            "EnableUserData": "false",
            "EnableImages": "false",
            "SortBy": "SortName",
            "SortOrder": "Ascending",
        },
    ):
        playlist_id = str(playlist.get("Id") or "")
        if not playlist_id or playlist_id in seen_playlist_ids:
            continue
        seen_playlist_ids.add(playlist_id)
        name = str(playlist.get("Name") or "Untitled playlist")
        item_ids = tuple(
            str(item_id)
            for item in _iter_playlist_items(base_url, headers, playlist_id, user_id)
            if (item_id := item.get("Id"))
        )
        playlists.append(PlaylistSnapshot(name=name, item_ids=item_ids))

    return UserCloneSnapshot(
        item_data=tuple(item_data.items()),
        playlists=tuple(playlists),
        item_descriptions=tuple(
            (item_id, name, item_type) for item_id, (name, item_type) in item_descriptions.items()
        ),
        duplicate_item_ids=tuple(sorted(duplicate_item_ids)),
        duplicate_rows_removed=duplicate_rows_removed,
    )


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
    state = _copyable_user_data({"Id": item_id, "UserData": payload})
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
        if _user_data_matches(expected_item_data[difference.item_id], direct_data):
            direct_matches += 1

    return UserCloneSnapshot(
        item_data=tuple(actual_item_data.items()),
        playlists=actual.playlists,
        item_descriptions=actual.item_descriptions,
        duplicate_item_ids=actual.duplicate_item_ids,
        duplicate_rows_removed=actual.duplicate_rows_removed,
        direct_item_checks=len(differences),
        direct_item_matches=direct_matches,
    )


def apply_user_snapshot(
    base_url: str,
    headers: dict[str, str],
    destination_user_id: str,
    snapshot: UserCloneSnapshot,
    existing_snapshot: UserCloneSnapshot | None = None,
) -> CloneCounts:
    """Copy missing Jellyfin state while reusing records already cloned correctly."""
    existing_item_data = dict(existing_snapshot.item_data) if existing_snapshot else {}
    user_data_updated = 0
    user_data_reused = 0
    for item_id, user_data in snapshot.item_data:
        existing_data = existing_item_data.get(item_id)
        if existing_data is not None and _user_data_matches(user_data, existing_data):
            user_data_reused += 1
            continue
        request_json(
            "POST",
            f"{base_url}/UserItems/{item_id}/UserData",
            service=SERVICE,
            headers=headers,
            params={"userId": destination_user_id},
            json={field: value for field, value in user_data.items() if field != "PlayedPercentage"},
        )
        user_data_updated += 1

    existing_playlists = existing_snapshot.playlists if existing_snapshot else ()
    playlists_created = 0
    playlists_reused = 0
    for playlist in snapshot.playlists:
        if any(_playlist_matches(playlist, existing) for existing in existing_playlists):
            playlists_reused += 1
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
        playlists_created += 1

    return CloneCounts(
        watch_items=sum(_has_watch_data(data) for _, data in snapshot.item_data),
        favorites=sum(bool(data.get("IsFavorite")) for _, data in snapshot.item_data),
        playlists=len(snapshot.playlists),
        playlist_items=sum(len(playlist.item_ids) for playlist in snapshot.playlists),
        user_data_updated=user_data_updated,
        user_data_reused=user_data_reused,
        playlists_created=playlists_created,
        playlists_reused=playlists_reused,
    )


def verify_user_snapshot(
    expected: UserCloneSnapshot,
    actual: UserCloneSnapshot,
) -> VerificationResult:
    """Compare destination watch state, favorites, and playlists with the source snapshot."""
    actual_item_data = dict(actual.item_data)
    descriptions = {item_id: (name, item_type) for item_id, name, item_type in expected.item_descriptions}
    missing_items = []
    mismatched_items = []
    matched_item_ids: set[str] = set()
    for item_id, expected_data in expected.item_data:
        name, item_type = descriptions.get(item_id, (item_id, "Unknown"))
        actual_data = actual_item_data.get(item_id)
        if actual_data is None:
            missing_items.append(
                ItemDifference(
                    item_id,
                    name,
                    item_type,
                    expected=tuple(expected_data.items()),
                )
            )
            continue
        mismatched_fields = _mismatched_user_data_fields(expected_data, actual_data)
        if mismatched_fields:
            mismatched_items.append(
                ItemDifference(
                    item_id,
                    name,
                    item_type,
                    mismatched_fields,
                    tuple(
                        (field, expected_data.get(field, _USER_DATA_DEFAULTS[field]))
                        for field in mismatched_fields
                    ),
                    tuple(
                        (field, actual_data.get(field, _USER_DATA_DEFAULTS[field]))
                        for field in mismatched_fields
                    ),
                )
            )
        else:
            matched_item_ids.add(item_id)

    expected_watch_ids = {item_id for item_id, user_data in expected.item_data if _has_watch_data(user_data)}
    expected_favorite_ids = {
        item_id for item_id, user_data in expected.item_data if bool(user_data.get("IsFavorite"))
    }
    matched_playlists = sum(
        any(_playlist_matches(playlist, destination) for destination in actual.playlists)
        for playlist in expected.playlists
    )
    missing_playlists = tuple(
        playlist.name
        for playlist in expected.playlists
        if not any(_playlist_matches(playlist, destination) for destination in actual.playlists)
    )

    return VerificationResult(
        user_data_expected=len(expected.item_data),
        user_data_matched=len(matched_item_ids),
        watch_items_expected=len(expected_watch_ids),
        watch_items_matched=len(expected_watch_ids & matched_item_ids),
        favorites_expected=len(expected_favorite_ids),
        favorites_matched=len(expected_favorite_ids & matched_item_ids),
        playlists_expected=len(expected.playlists),
        playlists_matched=matched_playlists,
        direct_item_checks=actual.direct_item_checks,
        direct_item_matches=actual.direct_item_matches,
        missing_items=tuple(missing_items),
        mismatched_items=tuple(mismatched_items),
        missing_playlists=missing_playlists,
        duplicate_source_item_ids=expected.duplicate_item_ids,
        duplicate_source_rows_removed=expected.duplicate_rows_removed,
    )
