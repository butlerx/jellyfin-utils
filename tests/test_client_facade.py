"""Public compatibility contract for the Jellyfin client facade."""

from jellyfin_utils import client
from jellyfin_utils.client import library, models, pagination, transport, watch

EXPECTED_EXPORTS = {
    "PAGE_SIZE": pagination.PAGE_SIZE,
    "LibraryItem": models.LibraryItem,
    "build_headers": transport.build_headers,
    "create_user": transport.create_user,
    "display_name": models.display_name,
    "drop_empty_series": library.drop_empty_series,
    "get_all_items": library.get_all_items,
    "get_json": transport.get_json,
    "get_users": transport.get_users,
    "get_watch_counts_per_item": watch.get_watch_counts_per_item,
    "get_watchers_per_item": watch.get_watchers_per_item,
    "iter_items": pagination.iter_items,
    "parse_last_played": library.parse_last_played,
    "post_empty": transport.post_empty,
    "roll_up_series_sizes": library.roll_up_series_sizes,
    "size_gb": models.size_gb,
}


def test_client_facade_reexports_the_complete_public_api() -> None:
    assert client.__all__ == list(EXPECTED_EXPORTS)
    for name, implementation in EXPECTED_EXPORTS.items():
        assert getattr(client, name) is implementation
