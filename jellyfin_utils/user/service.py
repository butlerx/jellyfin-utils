"""Compatibility facade for user snapshot models and operations."""

from jellyfin_utils.user.models import (
    CloneCounts,
    ItemDifference,
    PlaylistSnapshot,
    UserCloneSnapshot,
    VerificationResult,
)
from jellyfin_utils.user.repository import (
    _fetch_destination_user_data,
    _iter_playlist_items,
    apply_user_snapshot,
    capture_user_snapshot,
    resolve_destination_snapshot,
)
from jellyfin_utils.user.snapshot import (
    _copyable_user_data,
    _has_meaningful_user_data,
    _has_watch_data,
    _mismatched_user_data_fields,
    _normalise_user_data_value,
    _playlist_matches,
    _positive_number,
    _user_data_matches,
    verify_user_snapshot,
)

__all__ = [
    "CloneCounts",
    "ItemDifference",
    "PlaylistSnapshot",
    "UserCloneSnapshot",
    "VerificationResult",
    "_copyable_user_data",
    "_fetch_destination_user_data",
    "_has_meaningful_user_data",
    "_has_watch_data",
    "_iter_playlist_items",
    "_mismatched_user_data_fields",
    "_normalise_user_data_value",
    "_playlist_matches",
    "_positive_number",
    "_user_data_matches",
    "apply_user_snapshot",
    "capture_user_snapshot",
    "resolve_destination_snapshot",
    "verify_user_snapshot",
]
