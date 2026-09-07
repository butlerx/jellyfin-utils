"""Protect watched command behavior and serialized output contracts."""

from __future__ import annotations

import csv
import io
from dataclasses import replace
from typing import TYPE_CHECKING

import orjson
import pytest
from click.testing import CliRunner, Result
from conftest import BASE_URL, JELLYSEERR_URL, make_item

from jellyfin_utils.cli import cli
from jellyfin_utils.watched import cli as watched_cli

if TYPE_CHECKING:
    from jellyfin_utils.client import LibraryItem

RUNNER = CliRunner()
GIB = 1024**3


@pytest.fixture
def watched_scenario(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Stub a representative watched analysis without making HTTP requests."""
    movie = make_item(
        "movie-priority",
        name="Priority Movie",
        path="/media/priority.mkv",
        size=2 * GIB,
        tmdb_id=101,
    )
    series = replace(
        make_item(
            "series-rollup",
            "Series",
            name="Large Series",
            path="/media/series",
            size=5 * GIB,
            tmdb_id=102,
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
        "movie-priority": ["alice"],
        "series-rollup": ["alice", "bob"],
        "missing-path": ["alice", "bob"],
    }
    calls: dict[str, object] = {}

    monkeypatch.setattr(watched_cli, "build_headers", lambda _token: {"Authorization": "test"})
    monkeypatch.setattr(watched_cli, "get_users", lambda _base_url, _headers: users)
    monkeypatch.setattr(watched_cli, "get_all_items", lambda _base_url, _headers: items)
    monkeypatch.setattr(
        watched_cli,
        "get_requesters_by_tmdb_id",
        lambda _server, _token: {101: ("ALICE", "carol")},
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

    monkeypatch.setattr(watched_cli, "get_watchers_per_item", fake_watchers)
    return calls


def _invoke_watched(*extra: str) -> Result:
    return RUNNER.invoke(
        cli,
        [
            "watched",
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
            "--days",
            "30",
            "--threshold",
            "50",
            *extra,
        ],
    )


def test_watched_text_output_is_stable(watched_scenario: dict[str, object]) -> None:
    result = _invoke_watched()

    assert result.exit_code == 0, result.output
    assert result.output.endswith("\n")
    assert result.output.splitlines() == [
        "Total users: 3",
        "Ignoring users: ignored",
        "Active users analyzed: 2",
        "Watch threshold: 50% of users",
        "Total library items scanned: 3",
        "(Plays older than 30 days are ignored)",
        "PRIORITY = requested in Jellyseerr and watched by its requester",
        "",
        "Candidate items (watched by >=50% of users): 2",
        "Total size of candidates: 2.00 GB",
        "(Series sizes total their episodes and are left out of the figure above.)",
        "",
        "",
        "=" * 80,
        "Movies (1 items)",
        "=" * 80,
        "Priority | Title                                              | Watched        | Size",
        f"{'PRIORITY':<8} | {'Priority Movie':<50} | 1/2 users (50.0%) |   2.00 GB",
        "  Watched by: alice",
        "  Requested by: ALICE, carol",
        "  Requester watched: ALICE",
        "  ID: movie-priority",
        "  Path: /media/priority.mkv",
        "",
        "=" * 80,
        "Seriess (1 items)",
        "=" * 80,
        "Priority | Title                                              | Watched        | Size",
        f"{'standard':<8} | {'Large Series':<50} | 2/2 users (100.0%) |   5.00 GB",
        "  Watched by: alice, bob",
        "  ID: series-rollup",
        "  Path: /media/series",
    ]
    assert watched_scenario == {"ignored": {"ignored"}, "max_age_days": 30}


@pytest.mark.usefixtures("watched_scenario")
def test_watched_quiet_output_is_only_the_item_list() -> None:
    result = _invoke_watched("--quiet")

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        f"{'PRIORITY':<8} | {'Priority Movie':<50} | 1/2 users (50.0%) |   2.00 GB",
        f"{'standard':<8} | {'Large Series':<50} | 2/2 users (100.0%) |   5.00 GB",
    ]


@pytest.mark.usefixtures("watched_scenario")
def test_watched_json_contract_is_stable() -> None:
    result = _invoke_watched("--output", "json")

    assert result.exit_code == 0, result.output
    assert orjson.loads(result.output) == {
        "server": BASE_URL,
        "total_users": 3,
        "ignored_users": ["ignored"],
        "active_users": 2,
        "threshold_percent": 50,
        "total_items": 3,
        "max_age_days": 30,
        "jellyseerr_requester_watch_prioritization": True,
        "candidates_count": 2,
        "candidates_by_type": {"movies": 1, "series": 1, "episodes": 0},
        "candidates": [
            {
                "name": "Priority Movie",
                "type": "Movie",
                "id": "movie-priority",
                "path": "/media/priority.mkv",
                "size": 2 * GIB,
                "watch_count": 1,
                "watch_percentage": 50.0,
                "watched_by": ["alice"],
                "requested_by": ["ALICE", "carol"],
                "watched_by_requester": ["ALICE"],
                "requester_watched": True,
            },
            {
                "name": "Large Series",
                "type": "Series",
                "id": "series-rollup",
                "path": "/media/series",
                "size": 5 * GIB,
                "watch_count": 2,
                "watch_percentage": 100.0,
                "watched_by": ["alice", "bob"],
                "requested_by": [],
                "watched_by_requester": [],
                "requester_watched": False,
            },
        ],
    }


@pytest.mark.usefixtures("watched_scenario")
def test_watched_markdown_output_is_stable() -> None:
    result = _invoke_watched("--output", "markdown")

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "| Priority | Type | Title | Watched | Requested by | Requester watched | Size |",
        "| --- | --- | --- | --- | --- | --- | ---: |",
        "| Priority | Movie | Priority Movie | 1/2 (50.0%) | ALICE, carol | ALICE | 2.00 GB |",
        "| Standard | Series | Large Series | 2/2 (100.0%) | — | — | 5.00 GB |",
    ]


@pytest.mark.usefixtures("watched_scenario")
def test_watched_csv_output_is_stable() -> None:
    result = _invoke_watched("--output", "csv")

    assert result.exit_code == 0, result.output
    assert list(csv.reader(io.StringIO(result.output))) == [
        [
            "Type",
            "Name",
            "Watch Count",
            "Watch %",
            "Watched By",
            "Requester Watched",
            "Requested By",
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
            "ALICE",
            "ALICE, carol",
            "2.00",
            "movie-priority",
            "/media/priority.mkv",
        ],
        [
            "Series",
            "Large Series",
            "2",
            "100.0%",
            "alice, bob",
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
        ("text", "Candidate items (watched by >=80% of users): 0"),
        ("json", '"candidates_count": 0'),
        ("markdown", "| Priority | Type | Title | Watched | Requested by | Requester watched | Size |"),
        ("csv", "Type,Name,Watch Count,Watch %"),
    ],
)
def test_watched_empty_results_are_stable(
    monkeypatch: pytest.MonkeyPatch,
    output_format: str,
    expected: str,
) -> None:
    users = [{"Id": "alice-id", "Name": "alice"}]
    items: list[LibraryItem] = [make_item("unwatched", name="Unwatched")]
    monkeypatch.setattr(watched_cli, "build_headers", lambda _token: {})
    monkeypatch.setattr(watched_cli, "get_users", lambda _base_url, _headers: users)
    monkeypatch.setattr(watched_cli, "get_all_items", lambda _base_url, _headers: items)
    monkeypatch.setattr(
        watched_cli,
        "get_watchers_per_item",
        lambda _base_url, _headers, _users, _ignored, **_kwargs: {},
    )

    result = RUNNER.invoke(
        cli,
        ["watched", "--server", BASE_URL, "--token", "test-token", "--output", output_format],
    )

    assert result.exit_code == 0, result.output
    assert expected in result.output


def test_find_candidates_handles_no_active_users_and_missing_paths() -> None:
    on_disk = make_item("on-disk", name="On disk")
    missing_path = make_item("missing", name="Missing", path="")

    candidates = watched_cli.find_candidates(
        [on_disk, missing_path],
        {"on-disk": ["ignored"], "missing": ["ignored"]},
        total_active_users=0,
        threshold_percent=80,
    )

    assert [candidate.item.item_id for candidate in candidates] == ["on-disk"]
    assert candidates[0].watch_percentage == 0
