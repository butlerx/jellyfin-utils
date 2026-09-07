"""
Compatibility facade for the public Jellyfin client helpers.

Consumer imports from :mod:`jellyfin_utils.client` remain stable while implementation details
live in focused transport, pagination, library, watch, and model modules.
"""

from .library import drop_empty_series, get_all_items, parse_last_played, roll_up_series_sizes
from .models import LibraryItem, display_name, size_gb
from .pagination import PAGE_SIZE, iter_items
from .transport import build_headers, create_user, get_json, get_users, post_empty
from .watch import get_watch_counts_per_item, get_watchers_per_item

__all__ = [
    "PAGE_SIZE",
    "LibraryItem",
    "build_headers",
    "create_user",
    "display_name",
    "drop_empty_series",
    "get_all_items",
    "get_json",
    "get_users",
    "get_watch_counts_per_item",
    "get_watchers_per_item",
    "iter_items",
    "parse_last_played",
    "post_empty",
    "roll_up_series_sizes",
    "size_gb",
]
