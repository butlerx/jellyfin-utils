"""Tests for Jellyfin client transport helpers."""

from jellyfin_utils.client.transport import build_headers


def test_build_headers_uses_mediabrowser_authorization() -> None:
    assert build_headers("test-token") == {
        "Authorization": 'MediaBrowser Token="test-token"',
    }
