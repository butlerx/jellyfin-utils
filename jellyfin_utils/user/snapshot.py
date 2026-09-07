"""Pure normalization and comparison helpers for user snapshots."""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from jellyfin_utils.user.models import (
    CloneCounts,
    ItemDifference,
    PlaylistSnapshot,
    UserCloneSnapshot,
    VerificationResult,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

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


def positive_number(value: object) -> bool:
    """Return whether a value is a positive numeric user-data value."""
    return isinstance(value, int | float) and value > 0


def has_meaningful_user_data(data: dict[str, object]) -> bool:
    """Return whether copied user data contains state worth preserving."""
    return (
        bool(data.get("Played"))
        or positive_number(data.get("PlayedPercentage"))
        or positive_number(data.get("PlaybackPositionTicks"))
        or positive_number(data.get("PlayCount"))
        or bool(data.get("IsFavorite"))
        or data.get("Likes") is not None
        or data.get("Rating") is not None
        or bool(data.get("LastPlayedDate"))
    )


def has_watch_data(data: dict[str, object]) -> bool:
    """Return whether copied user data represents watch progress or history."""
    return (
        bool(data.get("Played"))
        or positive_number(data.get("PlayedPercentage"))
        or positive_number(data.get("PlaybackPositionTicks"))
        or positive_number(data.get("PlayCount"))
        or bool(data.get("LastPlayedDate"))
    )


def copyable_user_data(item: dict) -> tuple[str, dict[str, object]] | None:
    """Extract meaningful cloneable user data from one Jellyfin item row."""
    item_id = item.get("Id")
    raw_data = item.get("UserData")
    if not item_id or not isinstance(raw_data, dict):
        return None

    data = {field: raw_data[field] for field in _COPYABLE_USER_DATA_FIELDS if field in raw_data}
    if bool(item.get("IsFolder")) or item.get("Type") in _FOLDER_ITEM_TYPES:
        data = {
            field: value for field, value in data.items() if field not in _DERIVED_FOLDER_PLAY_STATE_FIELDS
        }
    if not has_meaningful_user_data(data):
        return None
    return str(item_id), data


def normalise_item_rows(items: Iterable[dict]) -> UserCloneSnapshot:
    """Normalize and deduplicate item-query rows without performing I/O."""
    item_data: dict[str, dict[str, object]] = {}
    descriptions: dict[str, tuple[str, str]] = {}
    duplicate_ids: set[str] = set()
    duplicate_rows_removed = 0
    for item in items:
        state = copyable_user_data(item)
        if state is None:
            continue
        item_id, user_data = state
        if item_id in item_data:
            duplicate_ids.add(item_id)
            duplicate_rows_removed += 1
        item_data[item_id] = user_data
        descriptions.setdefault(
            item_id,
            (str(item.get("Name") or item_id), str(item.get("Type") or "Unknown")),
        )

    return UserCloneSnapshot(
        item_data=tuple(item_data.items()),
        playlists=(),
        item_descriptions=tuple(
            (item_id, name, item_type) for item_id, (name, item_type) in descriptions.items()
        ),
        duplicate_item_ids=tuple(sorted(duplicate_ids)),
        duplicate_rows_removed=duplicate_rows_removed,
    )


def normalise_user_data_value(field: str, value: object) -> object:
    """Normalize values whose equivalent API representations can differ."""
    if field != "LastPlayedDate" or not isinstance(value, str):
        return value
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return value


def mismatched_user_data_fields(
    expected: dict[str, object],
    actual: dict[str, object],
) -> tuple[str, ...]:
    """Return copied fields whose normalized expected and actual values differ."""
    return tuple(
        field
        for field, default in _USER_DATA_DEFAULTS.items()
        if normalise_user_data_value(field, expected.get(field, default))
        != normalise_user_data_value(field, actual.get(field, default))
    )


def user_data_matches(expected: dict[str, object], actual: dict[str, object]) -> bool:
    """Return whether two user-data records match across all copied fields."""
    return not mismatched_user_data_fields(expected, actual)


def playlist_matches(expected: PlaylistSnapshot, actual: PlaylistSnapshot) -> bool:
    """Return whether playlist names and ordered item IDs match."""
    return expected.name.casefold() == actual.name.casefold() and expected.item_ids == actual.item_ids


def clone_counts(
    snapshot: UserCloneSnapshot,
    *,
    user_data_updated: int,
    user_data_reused: int,
    playlists_created: int,
    playlists_reused: int,
) -> CloneCounts:
    """Build clone counters from a source snapshot and write results."""
    return CloneCounts(
        watch_items=sum(has_watch_data(data) for _, data in snapshot.item_data),
        favorites=sum(bool(data.get("IsFavorite")) for _, data in snapshot.item_data),
        playlists=len(snapshot.playlists),
        playlist_items=sum(len(playlist.item_ids) for playlist in snapshot.playlists),
        user_data_updated=user_data_updated,
        user_data_reused=user_data_reused,
        playlists_created=playlists_created,
        playlists_reused=playlists_reused,
    )


def _compare_items(
    expected: UserCloneSnapshot,
    actual: UserCloneSnapshot,
) -> tuple[list[ItemDifference], list[ItemDifference], set[str]]:
    actual_data = dict(actual.item_data)
    descriptions = {item_id: (name, item_type) for item_id, name, item_type in expected.item_descriptions}
    missing: list[ItemDifference] = []
    mismatched: list[ItemDifference] = []
    matched: set[str] = set()
    for item_id, expected_data in expected.item_data:
        name, item_type = descriptions.get(item_id, (item_id, "Unknown"))
        destination_data = actual_data.get(item_id)
        if destination_data is None:
            missing.append(ItemDifference(item_id, name, item_type, expected=tuple(expected_data.items())))
            continue
        fields = mismatched_user_data_fields(expected_data, destination_data)
        if fields:
            mismatched.append(
                ItemDifference(
                    item_id,
                    name,
                    item_type,
                    fields,
                    tuple((field, expected_data.get(field, _USER_DATA_DEFAULTS[field])) for field in fields),
                    tuple(
                        (field, destination_data.get(field, _USER_DATA_DEFAULTS[field])) for field in fields
                    ),
                )
            )
        else:
            matched.add(item_id)
    return missing, mismatched, matched


def _missing_playlists(
    expected: tuple[PlaylistSnapshot, ...],
    actual: tuple[PlaylistSnapshot, ...],
) -> tuple[str, ...]:
    return tuple(
        playlist.name
        for playlist in expected
        if not any(playlist_matches(playlist, destination) for destination in actual)
    )


def verify_user_snapshot(
    expected: UserCloneSnapshot,
    actual: UserCloneSnapshot,
) -> VerificationResult:
    """Compare destination watch state, favorites, and playlists with the source snapshot."""
    missing_items, mismatched_items, matched_ids = _compare_items(expected, actual)
    watch_ids = {item_id for item_id, data in expected.item_data if has_watch_data(data)}
    favorite_ids = {item_id for item_id, data in expected.item_data if bool(data.get("IsFavorite"))}
    missing_playlists = _missing_playlists(expected.playlists, actual.playlists)

    return VerificationResult(
        user_data_expected=len(expected.item_data),
        user_data_matched=len(matched_ids),
        watch_items_expected=len(watch_ids),
        watch_items_matched=len(watch_ids & matched_ids),
        favorites_expected=len(favorite_ids),
        favorites_matched=len(favorite_ids & matched_ids),
        playlists_expected=len(expected.playlists),
        playlists_matched=len(expected.playlists) - len(missing_playlists),
        direct_item_checks=actual.direct_item_checks,
        direct_item_matches=actual.direct_item_matches,
        missing_items=tuple(missing_items),
        mismatched_items=tuple(mismatched_items),
        missing_playlists=missing_playlists,
        duplicate_source_item_ids=expected.duplicate_item_ids,
        duplicate_source_rows_removed=expected.duplicate_rows_removed,
    )


# Private aliases preserve compatibility for callers that imported the old service internals.
_positive_number = positive_number
_has_meaningful_user_data = has_meaningful_user_data
_has_watch_data = has_watch_data
_copyable_user_data = copyable_user_data
_normalise_user_data_value = normalise_user_data_value
_mismatched_user_data_fields = mismatched_user_data_fields
_user_data_matches = user_data_matches
_playlist_matches = playlist_matches
