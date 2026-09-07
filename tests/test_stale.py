"""Characterization tests for the stale command and its output contracts."""

from __future__ import annotations

import csv
import datetime as dt
import io
from dataclasses import replace
from typing import TYPE_CHECKING

import orjson
import pytest
from click.testing import CliRunner, Result
from conftest import BASE_URL, JELLYSEERR_URL, make_item

from jellyfin_utils.cli import cli
from jellyfin_utils.stale import cli as stale_cli

if TYPE_CHECKING:
    from jellyfin_utils.client import LibraryItem

RUNNER = CliRunner()
GIB = 1024**3


@pytest.fixture
def stale_scenario(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Stub a representative stale analysis without making HTTP requests."""
    movie = make_item(
        "movie-priority",
        name="Priority Movie",
        path="/media/priority.mkv",
        size=2 * GIB,
        tmdb_id=201,
    )
    series = replace(
        make_item(
            "series-rollup",
            "Series",
            name="Large Series",
            path="/media/series",
            size=5 * GIB,
            tmdb_id=202,
        ),
        size_is_rollup=True,
    )
    missing_path = make_item(
        "missing-path",
        name="Missing Path",
        path="",
        size=9 * GIB,
    )
    users = [
        {"Id": "alice-id", "Name": "alice"},
        {"Id": "bob-id", "Name": "bob"},
        {"Id": "ignored-id", "Name": "ignored"},
    ]
    items = [movie, series, missing_path]
    watchers = {
        "movie-priority": ["ALICE"],
        "series-rollup": [],
        "missing-path": [],
    }
    calls: dict[str, object] = {}

    monkeypatch.setattr(stale_cli, "build_headers", lambda _token: {"Authorization": "test"})
    monkeypatch.setattr(stale_cli, "get_users", lambda _base_url, _headers: users)
    monkeypatch.setattr(stale_cli, "get_all_items", lambda _base_url, _headers: items)
    monkeypatch.setattr(
        stale_cli,
        "get_requesters_by_tmdb_id",
        lambda _server, _token: {201: ("alice", "carol")},
    )

    def fake_watchers(
        _base_url: str,
        _headers: dict[str, str],
        _users: list[dict],
        ignored: set[str],
        max_age_days: int | None,
    ) -> dict[str, list[str]]:
        calls["ignored"] = ignored
        calls["max_age_days"] = max_age_days
        return watchers

    monkeypatch.setattr(stale_cli, "get_watchers_per_item", fake_watchers)
    return calls


def _invoke_stale(*extra: str) -> Result:
    return RUNNER.invoke(
        cli,
        [
            "stale",
            "--server",
            BASE_URL,
            "--token",
            "test-token",
            "--jellyseerr-server",
            JELLYSEERR_URL,
            "--jellyseerr-token",
            "jellyseerr-token",
            "--ignore-user",
            "ignored",
            "--min-age",
            "30",
            "--max-watchers",
            "1",
            *extra,
        ],
    )


def test_stale_text_output_is_stable(stale_scenario: dict[str, object]) -> None:
    result = _invoke_stale()

    assert result.exit_code == 0, result.output
    assert result.output.endswith("\n")
    assert result.output.splitlines() == [
        "Total users: 3",
        "Ignoring users: ignored",
        "Active users analyzed: 2",
        "Stale threshold: watched by <= 1 users",
        "Total library items scanned: 3",
        "Minimum age: 30 days (newer items excluded)",
        "PRIORITY = requested in Jellyseerr and watched by its requester",
        "",
        "Stale items (watched by <=1 users): 2",
        "Total size of stale content: 2.00 GB",
        "(Series sizes total their episodes and are left out of the figure above.)",
        "",
        "",
        "=" * 80,
        "Movies (1 items)",
        "=" * 80,
        "Priority | Title                                              | Watched        | Size      | Age",
        f"{'PRIORITY':<8} | {'Priority Movie':<50} | 1/2 users (50.0%) |   2.00 GB | age unknown",
        "  Requested by: alice, carol",
        "  Requester watched: alice",
        "  ID: movie-priority",
        "  Path: /media/priority.mkv",
        "",
        "=" * 80,
        "Seriess (1 items)",
        "=" * 80,
        "Priority | Title                                              | Watched        | Size      | Age",
        f"{'standard':<8} | {'Large Series':<50} | 0/2 users (0.0%) |   5.00 GB | age unknown",
        "  ID: series-rollup",
        "  Path: /media/series",
    ]
    assert stale_scenario == {"ignored": {"ignored"}, "max_age_days": None}


@pytest.mark.usefixtures("stale_scenario")
def test_stale_quiet_output_is_only_the_item_list() -> None:
    result = _invoke_stale("--quiet")

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        f"{'PRIORITY':<8} | {'Priority Movie':<50} | 1/2 users (50.0%) |   2.00 GB | age unknown",
        f"{'standard':<8} | {'Large Series':<50} | 0/2 users (0.0%) |   5.00 GB | age unknown",
    ]


@pytest.mark.usefixtures("stale_scenario")
def test_stale_json_contract_is_stable() -> None:
    result = _invoke_stale("--output", "json")

    assert result.exit_code == 0, result.output
    assert orjson.loads(result.output) == {
        "server": BASE_URL,
        "total_users": 3,
        "ignored_users": ["ignored"],
        "active_users": 2,
        "max_watchers": 1,
        "min_age_days": 30,
        "jellyseerr_requester_watch_prioritization": True,
        "total_items": 3,
        "stale_count": 2,
        "stale_by_type": {"movies": 1, "series": 1, "episodes": 0},
        "stale_items": [
            {
                "name": "Priority Movie",
                "type": "Movie",
                "id": "movie-priority",
                "path": "/media/priority.mkv",
                "size": 2 * GIB,
                "watch_count": 1,
                "watch_percentage": 50.0,
                "age_days": None,
                "requested_by": ["alice", "carol"],
                "watched_by_requester": ["alice"],
                "requester_watched": True,
            },
            {
                "name": "Large Series",
                "type": "Series",
                "id": "series-rollup",
                "path": "/media/series",
                "size": 5 * GIB,
                "watch_count": 0,
                "watch_percentage": 0.0,
                "age_days": None,
                "requested_by": [],
                "watched_by_requester": [],
                "requester_watched": False,
            },
        ],
    }


@pytest.mark.usefixtures("stale_scenario")
def test_stale_markdown_output_is_stable() -> None:
    result = _invoke_stale("--output", "markdown")

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "| Priority | Type | Title | Watched | Requested by | Requester watched | Age | Size |",
        "| --- | --- | --- | --- | --- | --- | --- | ---: |",
        "| Priority | Movie | Priority Movie | 1/2 (50.0%) | alice, carol | alice | — | 2.00 GB |",
        "| Standard | Series | Large Series | 0/2 (0.0%) | — | — | — | 5.00 GB |",
    ]


@pytest.mark.usefixtures("stale_scenario")
def test_stale_csv_output_is_stable() -> None:
    result = _invoke_stale("--output", "csv")

    assert result.exit_code == 0, result.output
    assert list(csv.reader(io.StringIO(result.output))) == [
        [
            "Type",
            "Name",
            "Watch Count",
            "Watch %",
            "Requester Watched",
            "Requested By",
            "Age (days)",
            "File Size (GB)",
            "ID",
            "Path",
        ],
        [
            "Movie",
            "Priority Movie",
            "1",
            "50.0%",
            "alice",
            "alice, carol",
            "",
            "2.00",
            "movie-priority",
            "/media/priority.mkv",
        ],
        [
            "Series",
            "Large Series",
            "0",
            "0.0%",
            "",
            "",
            "",
            "5.00",
            "series-rollup",
            "/media/series",
        ],
    ]


@pytest.mark.parametrize(
    ("output_format", "expected"),
    [
        ("text", "Stale items (watched by <=0 users): 0"),
        ("json", '"stale_count": 0'),
        (
            "markdown",
            "| Priority | Type | Title | Watched | Requested by | Requester watched | Age | Size |",
        ),
        ("csv", "Type,Name,Watch Count,Watch %"),
    ],
)
def test_stale_empty_results_are_stable(
    monkeypatch: pytest.MonkeyPatch,
    output_format: str,
    expected: str,
) -> None:
    users = [{"Id": "alice-id", "Name": "alice"}]
    items: list[LibraryItem] = [make_item("watched", name="Watched")]
    monkeypatch.setattr(stale_cli, "build_headers", lambda _token: {})
    monkeypatch.setattr(stale_cli, "get_users", lambda _base_url, _headers: users)
    monkeypatch.setattr(stale_cli, "get_all_items", lambda _base_url, _headers: items)
    monkeypatch.setattr(
        stale_cli,
        "get_watchers_per_item",
        lambda _base_url, _headers, _users, _ignored, **_kwargs: {"watched": ["alice"]},
    )

    result = RUNNER.invoke(
        cli,
        ["stale", "--server", BASE_URL, "--token", "test-token", "--output", output_format],
    )

    assert result.exit_code == 0, result.output
    assert expected in result.output


def test_find_stale_applies_watcher_age_and_path_boundaries() -> None:
    now = dt.datetime(2026, 1, 31, tzinfo=dt.UTC)
    old = replace(make_item("old"), date_created=now - dt.timedelta(days=31))
    cutoff = replace(make_item("cutoff"), date_created=now - dt.timedelta(days=30))
    new = replace(make_item("new"), date_created=now - dt.timedelta(days=29))
    over_watcher_limit = replace(make_item("popular"), date_created=now - dt.timedelta(days=31))
    missing_path = replace(make_item("missing", path=""), date_created=now - dt.timedelta(days=31))

    stale_items = stale_cli.find_stale(
        [old, cutoff, new, over_watcher_limit, missing_path],
        {
            "old": [],
            "cutoff": ["alice"],
            "new": [],
            "popular": ["alice", "bob"],
            "missing": [],
        },
        total_active_users=2,
        max_watchers=1,
        min_age_days=30,
        now=now,
    )

    assert [item.item.item_id for item in stale_items] == ["old", "cutoff"]
    assert [item.age_days for item in stale_items] == [31, 30]


def test_find_stale_orders_by_requester_size_then_watch_count() -> None:
    now = dt.datetime(2026, 1, 31, tzinfo=dt.UTC)
    priority_small = make_item("priority", size=GIB, tmdb_id=901)
    large_watched = make_item("large-watched", size=5 * GIB)
    large_unwatched = make_item("large-unwatched", size=5 * GIB)

    stale_items = stale_cli.find_stale(
        [large_watched, priority_small, large_unwatched],
        {
            "priority": ["ALICE"],
            "large-watched": ["bob"],
            "large-unwatched": [],
        },
        total_active_users=2,
        max_watchers=1,
        min_age_days=None,
        now=now,
        requesters_by_tmdb_id={901: ("alice",)},
    )

    assert [item.item.item_id for item in stale_items] == [
        "priority",
        "large-unwatched",
        "large-watched",
    ]


def test_stale_watch_percentage_handles_no_active_users() -> None:
    now = dt.datetime(2026, 1, 31, tzinfo=dt.UTC)
    [item] = stale_cli.find_stale(
        [make_item("unwatched")],
        {"unwatched": []},
        total_active_users=0,
        max_watchers=0,
        min_age_days=None,
        now=now,
    )

    assert item.watch_percentage == 0.0
