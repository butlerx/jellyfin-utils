"""Protect reclaim command behavior and serialized output contracts."""

from __future__ import annotations

import csv
import io
from dataclasses import replace
from typing import TYPE_CHECKING

import orjson
import pytest
from click.testing import CliRunner, Result
from conftest import BASE_URL, JELLYSEERR_URL, make_item

from jellyfin_utils.analysis import cli as analysis_cli
from jellyfin_utils.cli import cli
from jellyfin_utils.stale.models import StaleItem
from jellyfin_utils.watched.models import Candidate

if TYPE_CHECKING:
    from jellyfin_utils.client import LibraryItem

RUNNER = CliRunner()
GIB = 1024**3


@pytest.fixture
def reclaim_scenario(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Stub all three reclaim classifications without making HTTP requests."""
    both = make_item(
        "both",
        name="Both Movie",
        path="/media/both.mkv",
        size=3 * GIB,
        tmdb_id=301,
    )
    watched = make_item(
        "watched",
        name="Watched Movie",
        path="/media/watched.mkv",
        size=5 * GIB,
        tmdb_id=302,
    )
    stale = replace(
        make_item(
            "stale",
            "Series",
            name="Stale Series",
            path="/media/stale",
            size=4 * GIB,
            tmdb_id=303,
        ),
        size_is_rollup=True,
    )
    users = [
        {"Id": "alice-id", "Name": "alice"},
        {"Id": "bob-id", "Name": "bob"},
    ]
    items = [both, watched, stale]
    candidates = [
        Candidate(
            item=both,
            watch_count=1,
            watch_percentage=50.0,
            watched_by=("alice",),
            requested_by=("alice",),
            watched_by_requester=("alice",),
        ),
        Candidate(
            item=watched,
            watch_count=2,
            watch_percentage=100.0,
            watched_by=("alice", "bob"),
        ),
    ]
    stale_items = [
        StaleItem(
            item=both,
            watch_count=1,
            total_users=2,
            age_days=120,
            requested_by=("alice",),
            watched_by_requester=("alice",),
        ),
        StaleItem(item=stale, watch_count=0, total_users=2, age_days=180),
    ]
    calls: dict[str, object] = {}

    monkeypatch.setattr(analysis_cli, "build_headers", lambda _token: {"Authorization": "test"})
    monkeypatch.setattr(analysis_cli, "get_users", lambda _base_url, _headers: users)
    monkeypatch.setattr(analysis_cli, "get_all_items", lambda _base_url, _headers: items)
    monkeypatch.setattr(
        analysis_cli,
        "get_watchers_per_item",
        lambda _base_url, _headers, _users, _ignored, **_kwargs: {},
    )
    monkeypatch.setattr(
        analysis_cli,
        "get_requesters_by_tmdb_id",
        lambda _server, _token: {301: ("alice",)},
    )

    def fake_candidates(
        _items: list[LibraryItem],
        _watchers: dict[str, list[str]],
        _active: int,
        threshold: int,
        requesters: dict[int, tuple[str, ...]] | None,
    ) -> list[Candidate]:
        calls["candidate_threshold"] = threshold
        calls["candidate_requesters"] = requesters
        return candidates

    def fake_stale(
        _items: list[LibraryItem],
        _watchers: dict[str, list[str]],
        _active: int,
        max_watchers: int,
        min_age: int,
        _now: object,
        requesters: dict[int, tuple[str, ...]] | None,
    ) -> list[StaleItem]:
        calls["stale_max_watchers"] = max_watchers
        calls["stale_min_age"] = min_age
        calls["stale_requesters"] = requesters
        return stale_items

    monkeypatch.setattr(analysis_cli, "find_candidates", fake_candidates)
    monkeypatch.setattr(analysis_cli, "find_stale", fake_stale)
    return calls


def _invoke_reclaim(*extra: str) -> Result:
    return RUNNER.invoke(
        cli,
        [
            "reclaim",
            "--server",
            BASE_URL,
            "--token",
            "test-token",
            "--jellyseerr-server",
            JELLYSEERR_URL,
            "--jellyseerr-token",
            "jellyseerr-token",
            "--threshold",
            "50",
            "--min-age",
            "30",
            *extra,
        ],
    )


def test_reclaim_text_output_is_stable(reclaim_scenario: dict[str, object]) -> None:
    result = _invoke_reclaim()

    assert result.exit_code == 0, result.output
    assert result.output.endswith("\n")
    assert result.output.splitlines() == [
        "Reclaim review · 3 candidates · 8.00 GiB to reclaim",
        "Series sizes total their episodes, which are listed separately and counted once.",
        "* = requested in Jellyseerr and already watched by whoever requested it",
        "",
        "── Widely watched, and stale · 1 item · 3.00 GiB ───────────────────────────────",
        f"* {'Movie':<7} {'Both Movie':<32}  {'1 watcher':>11}  {'3.00 GiB':>10}",
        "          requested by alice, who has since watched it",
        "          /media/both.mkv  [both]",
        "",
        "── Widely watched · 1 item · 5.00 GiB ──────────────────────────────────────────",
        f"  {'Movie':<7} {'Watched Movie':<32}  {'2 watchers':>11}  {'5.00 GiB':>10}",
        "          /media/watched.mkv  [watched]",
        "",
        "── Stale · 1 item · 0.00 GiB ───────────────────────────────────────────────────",
        f"  {'Series':<7} {'Stale Series':<32}  {'0 watchers':>11}  {'4.00 GiB':>10}",
        "          /media/stale  [stale]",
    ]
    assert reclaim_scenario == {
        "candidate_threshold": 50,
        "candidate_requesters": {301: ("alice",)},
        "stale_max_watchers": 0,
        "stale_min_age": 30,
        "stale_requesters": {301: ("alice",)},
    }


@pytest.mark.usefixtures("reclaim_scenario")
def test_reclaim_quiet_output_is_only_the_item_list() -> None:
    result = _invoke_reclaim("--quiet")

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        f"* {'Movie':<7} {'Both Movie':<32}  {'1 watcher':>11}  {'3.00 GiB':>10}",
        f"  {'Movie':<7} {'Watched Movie':<32}  {'2 watchers':>11}  {'5.00 GiB':>10}",
        f"  {'Series':<7} {'Stale Series':<32}  {'0 watchers':>11}  {'4.00 GiB':>10}",
    ]


@pytest.mark.usefixtures("reclaim_scenario")
def test_reclaim_json_contract_preserves_reasons_and_order() -> None:
    result = _invoke_reclaim("--output", "json")

    assert result.exit_code == 0, result.output
    assert orjson.loads(result.output) == {
        "candidates": [
            {
                "reason": "widely_watched_and_stale",
                "item": "Both Movie",
                "series": None,
                "id": "both",
                "type": "Movie",
                "path": "/media/both.mkv",
                "size_gib": 3.0,
                "size_is_rollup": False,
                "watchers": 1,
                "requested_by": ["alice"],
                "watched_by_requester": ["alice"],
                "requester_watched": True,
            },
            {
                "reason": "widely_watched",
                "item": "Watched Movie",
                "series": None,
                "id": "watched",
                "type": "Movie",
                "path": "/media/watched.mkv",
                "size_gib": 5.0,
                "size_is_rollup": False,
                "watchers": 2,
                "requested_by": [],
                "watched_by_requester": [],
                "requester_watched": False,
            },
            {
                "reason": "stale",
                "item": "Stale Series",
                "series": None,
                "id": "stale",
                "type": "Series",
                "path": "/media/stale",
                "size_gib": 4.0,
                "size_is_rollup": True,
                "watchers": 0,
                "requested_by": [],
                "watched_by_requester": [],
                "requester_watched": False,
            },
        ],
        "count": 3,
        "estimated_reclaimable_gib": 8.0,
        "jellyseerr_enabled": True,
    }


@pytest.mark.usefixtures("reclaim_scenario")
def test_reclaim_markdown_output_is_stable() -> None:
    result = _invoke_reclaim("--output", "markdown")

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "| Priority | Reason | Type | Series | Title | Watchers | Requested by | Requester watched | Size |",
        "| --- | --- | --- | --- | --- | ---: | --- | --- | ---: |",
        "| Priority | widely_watched_and_stale | Movie | — | Both Movie | 1 | alice | alice | 3.00 GiB |",
        "| Standard | widely_watched | Movie | — | Watched Movie | 2 | — | — | 5.00 GiB |",
        "| Standard | stale | Series | — | Stale Series | 0 | — | — | 4.00 GiB |",
    ]


@pytest.mark.usefixtures("reclaim_scenario")
def test_reclaim_csv_output_is_stable() -> None:
    result = _invoke_reclaim("--output", "csv")

    assert result.exit_code == 0, result.output
    assert list(csv.reader(io.StringIO(result.output))) == [
        [
            "Reason",
            "Type",
            "Series",
            "Name",
            "Watchers",
            "Requester Watched",
            "Requested By",
            "File Size (GiB)",
            "ID",
            "Path",
        ],
        [
            "widely_watched_and_stale",
            "Movie",
            "",
            "Both Movie",
            "1",
            "alice",
            "alice",
            "3.00",
            "both",
            "/media/both.mkv",
        ],
        [
            "widely_watched",
            "Movie",
            "",
            "Watched Movie",
            "2",
            "",
            "",
            "5.00",
            "watched",
            "/media/watched.mkv",
        ],
        [
            "stale",
            "Series",
            "",
            "Stale Series",
            "0",
            "",
            "",
            "4.00",
            "stale",
            "/media/stale",
        ],
    ]


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ((), "Nothing to review."),
        (("--quiet",), "\n"),
        (("--output", "json"), '"count": 0'),
        (("--output", "markdown"), "| Priority | Reason | Type | Series | Title |"),
        (("--output", "csv"), "Reason,Type,Series,Name,Watchers"),
    ],
)
def test_reclaim_empty_results_are_stable(
    monkeypatch: pytest.MonkeyPatch,
    extra: tuple[str, ...],
    expected: str,
) -> None:
    _stub_empty_reclaim(monkeypatch)

    result = RUNNER.invoke(
        cli,
        ["reclaim", "--server", BASE_URL, "--token", "test-token", *extra],
    )

    assert result.exit_code == 0, result.output
    if extra == ("--quiet",):
        assert result.output == expected
    else:
        assert expected in result.output


def test_reclaim_without_jellyseerr_passes_no_requester_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub_empty_reclaim(monkeypatch)

    result = RUNNER.invoke(
        cli,
        ["reclaim", "--server", BASE_URL, "--token", "test-token", "--output", "json"],
    )

    assert result.exit_code == 0, result.output
    assert orjson.loads(result.output)["jellyseerr_enabled"] is False
    assert calls == {"candidate_requesters": None, "stale_requesters": None}


def _stub_empty_reclaim(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    calls: dict[str, object] = {}
    monkeypatch.setattr(analysis_cli, "build_headers", lambda _token: {})
    monkeypatch.setattr(analysis_cli, "get_users", lambda _base_url, _headers: [])
    monkeypatch.setattr(analysis_cli, "get_all_items", lambda _base_url, _headers: [])
    monkeypatch.setattr(
        analysis_cli,
        "get_watchers_per_item",
        lambda _base_url, _headers, _users, _ignored, **_kwargs: {},
    )

    def no_candidates(
        _items: list[LibraryItem],
        _watchers: dict[str, list[str]],
        _active: int,
        _threshold: int,
        requesters: dict[int, tuple[str, ...]] | None,
    ) -> list[Candidate]:
        calls["candidate_requesters"] = requesters
        return []

    def no_stale(
        _items: list[LibraryItem],
        _watchers: dict[str, list[str]],
        _active: int,
        _max_watchers: int,
        _min_age: int,
        _now: object,
        requesters: dict[int, tuple[str, ...]] | None,
    ) -> list[StaleItem]:
        calls["stale_requesters"] = requesters
        return []

    monkeypatch.setattr(analysis_cli, "find_candidates", no_candidates)
    monkeypatch.setattr(analysis_cli, "find_stale", no_stale)
    return calls
