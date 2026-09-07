"""Immutable boundary and result types for Jellyfin user cloning."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


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


PasswordResolver = Callable[[str | None, bool], str | None]


@dataclass(frozen=True)
class CloneUserRequest:
    """Validated inputs and deferred password input for one clone operation."""

    source_username: str
    username: str
    base_url: str
    token: str = field(repr=False)
    jellyseerr_server: str
    jellyseerr_token: str = field(repr=False)
    email: str
    password: str | None = field(repr=False)
    no_password: bool
    password_resolver: PasswordResolver = field(repr=False, compare=False)


@dataclass(frozen=True)
class CloneUserOutcome:
    """All data needed to render either a completed or failed clone."""

    source_name: str
    destination_name: str
    email: str
    jellyfin_user_id: str
    counts: CloneCounts
    verification: VerificationResult
    destination_created: bool
    password_set: bool | None
    jellyseerr_user_id: int | None = None

    @property
    def completed(self) -> bool:
        """Return whether verification passed and Jellyseerr setup completed."""
        return self.verification.verified and self.jellyseerr_user_id is not None
