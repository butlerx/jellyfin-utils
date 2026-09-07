"""Pure user-snapshot normalization and comparison tests."""

from jellyfin_utils.user.models import PlaylistSnapshot, UserCloneSnapshot
from jellyfin_utils.user.snapshot import normalise_item_rows, verify_user_snapshot


def test_normalise_item_rows_deduplicates_and_ignores_derived_folder_watch_state() -> None:
    snapshot = normalise_item_rows(
        [
            {
                "Id": "movie",
                "Name": "Original name",
                "Type": "Movie",
                "UserData": {"IsFavorite": True, "Played": False},
            },
            {
                "Id": "movie",
                "Name": "Duplicate name",
                "Type": "Movie",
                "UserData": {"IsFavorite": True, "Played": True},
            },
            {
                "Id": "folder",
                "Name": "Favorite collection",
                "Type": "BoxSet",
                "IsFolder": True,
                "UserData": {"IsFavorite": True, "Played": True, "PlayCount": 3},
            },
            {
                "Id": "empty",
                "Type": "Movie",
                "UserData": {"IsFavorite": False, "Played": False},
            },
        ]
    )

    assert snapshot.item_data == (
        ("movie", {"IsFavorite": True, "Played": True}),
        ("folder", {"IsFavorite": True}),
    )
    assert snapshot.item_descriptions == (
        ("movie", "Original name", "Movie"),
        ("folder", "Favorite collection", "BoxSet"),
    )
    assert snapshot.duplicate_item_ids == ("movie",)
    assert snapshot.duplicate_rows_removed == 1


def test_verify_user_snapshot_normalises_dates_and_matches_playlist_names_case_insensitively() -> None:
    expected = UserCloneSnapshot(
        item_data=(("movie", {"Played": True, "LastPlayedDate": "2026-08-01T12:00:00Z"}),),
        playlists=(PlaylistSnapshot("Road Trip", ("one", "two")),),
    )
    actual = UserCloneSnapshot(
        item_data=(("movie", {"Played": True, "LastPlayedDate": "2026-08-01T12:00:00+00:00"}),),
        playlists=(PlaylistSnapshot("road trip", ("one", "two")),),
        direct_item_checks=1,
        direct_item_matches=1,
    )

    result = verify_user_snapshot(expected, actual)

    assert result.verified is True
    assert result.user_data_matched == 1
    assert result.playlists_matched == 1
    assert result.direct_item_checks == 1
    assert result.direct_item_matches == 1


def test_verify_user_snapshot_reports_field_values_and_playlist_order_changes() -> None:
    expected = UserCloneSnapshot(
        item_data=(("movie", {"PlayCount": 2, "IsFavorite": True}),),
        playlists=(PlaylistSnapshot("Road Trip", ("one", "two")),),
        item_descriptions=(("movie", "Example Movie", "Movie"),),
        duplicate_item_ids=("movie",),
        duplicate_rows_removed=1,
    )
    actual = UserCloneSnapshot(
        item_data=(("movie", {"PlayCount": 1, "IsFavorite": True}),),
        playlists=(PlaylistSnapshot("Road Trip", ("two", "one")),),
    )

    result = verify_user_snapshot(expected, actual)

    assert result.verified is False
    assert result.missing_items == ()
    assert result.mismatched_item_ids == ("movie",)
    assert result.mismatched_items[0].name == "Example Movie"
    assert result.mismatched_items[0].fields == ("PlayCount",)
    assert dict(result.mismatched_items[0].expected) == {"PlayCount": 2}
    assert dict(result.mismatched_items[0].actual) == {"PlayCount": 1}
    assert result.missing_playlists == ("Road Trip",)
    assert result.duplicate_source_item_ids == ("movie",)
    assert result.duplicate_source_rows_removed == 1
