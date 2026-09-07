"""Shared Jellyfin and Jellyseerr data acquisition for media analysis."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from jellyfin_utils.client import build_headers, get_all_items, get_users, get_watchers_per_item
from jellyfin_utils.jellyseerr import get_requesters_by_tmdb_id
from jellyfin_utils.options import require_jellyseerr_pair

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from jellyfin_utils.client import LibraryItem

    BuildHeaders = Callable[[str], dict[str, str]]
    GetUsers = Callable[[str, dict[str, str]], list[dict]]
    GetItems = Callable[[str, dict[str, str]], list[LibraryItem]]
    GetWatchers = Callable[..., dict[str, list[str]]]
    GetRequesters = Callable[[str, str], dict[int, tuple[str, ...]]]


@dataclass(frozen=True)
class MediaAnalysisContext:
    """Immutable inputs and derived totals shared by media analyses."""

    base_url: str
    users: tuple[Mapping[str, object], ...]
    items: tuple[LibraryItem, ...]
    ignored_usernames: frozenset[str]
    active_user_count: int
    watchers: Mapping[str, tuple[str, ...]]
    requesters_by_tmdb_id: Mapping[int, tuple[str, ...]] | None

    @property
    def total_users(self) -> int:
        """Return the number of Jellyfin users loaded for the analysis."""
        return len(self.users)

    @property
    def total_items(self) -> int:
        """Return the number of Jellyfin library items scanned."""
        return len(self.items)

    @property
    def jellyseerr_enabled(self) -> bool:
        """Whether requester data was loaded from Jellyseerr."""
        return self.requesters_by_tmdb_id is not None


def load_media_analysis(
    base_url: str,
    token: str,
    ignore_usernames: Sequence[str],
    *,
    max_watch_age_days: int | None,
    jellyseerr_server: str | None,
    jellyseerr_token: str | None,
    build_headers_fn: BuildHeaders = build_headers,
    get_users_fn: GetUsers = get_users,
    get_watchers_fn: GetWatchers = get_watchers_per_item,
    get_items_fn: GetItems = get_all_items,
    get_requesters_fn: GetRequesters = get_requesters_by_tmdb_id,
) -> MediaAnalysisContext:
    """Load all shared inputs for watched, stale, and reclaim analyses."""
    require_jellyseerr_pair(jellyseerr_server, jellyseerr_token)

    headers = build_headers_fn(token)
    users = get_users_fn(base_url, headers)
    ignored = set(ignore_usernames)
    watchers = get_watchers_fn(
        base_url,
        headers,
        users,
        ignored,
        max_age_days=max_watch_age_days,
    )
    items = get_items_fn(base_url, headers)
    requesters = (
        get_requesters_fn(jellyseerr_server, jellyseerr_token)
        if jellyseerr_server is not None and jellyseerr_token is not None
        else None
    )

    return MediaAnalysisContext(
        base_url=base_url,
        users=tuple(MappingProxyType(user.copy()) for user in users),
        items=tuple(items),
        ignored_usernames=frozenset(ignored),
        active_user_count=sum(user.get("Name") not in ignored for user in users),
        watchers=MappingProxyType({item_id: tuple(usernames) for item_id, usernames in watchers.items()}),
        requesters_by_tmdb_id=(MappingProxyType(dict(requesters)) if requesters is not None else None),
    )
