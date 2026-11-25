"""``user progress``: episode states, season roll-ups, series matching, and the report."""

from __future__ import annotations

import datetime as dt

import click
import pytest

from jellyfin_utils.output import OutputFormat, Report
from jellyfin_utils.user import progress
from jellyfin_utils.user.progress import EpisodeProgress, EpisodeState, summarise_seasons

GIB = 1024**3
TODAY = dt.date(2026, 11, 25)


def _episode(
    season: int,
    number: int,
    *,
    on_disk: bool = True,
    played: bool = False,
    last_played: str | None = None,
    premiere: str | None = "2025-01-01T00:00:00Z",
    size: int = GIB,
) -> dict:
    user_data: dict = {"Played": played}
    if last_played:
        user_data["LastPlayedDate"] = last_played
    raw: dict = {
        "Name": f"Episode {number}",
        "ParentIndexNumber": season,
        "IndexNumber": number,
        "LocationType": "FileSystem" if on_disk else "Virtual",
        "UserData": user_data,
        "PremiereDate": premiere,
    }
    if on_disk:
        raw["Path"] = f"/tv/show/S{season}E{number}.mkv"
        raw["MediaSources"] = [{"Size": size}]
    return raw


@pytest.mark.parametrize(
    ("raw", "state"),
    [
        (_episode(1, 1, played=True), EpisodeState.PLAYED),
        (_episode(1, 1, last_played="2026-05-03T20:00:00Z"), EpisodeState.STARTED),
        (_episode(1, 1), EpisodeState.UNPLAYED),
        (_episode(1, 1, on_disk=False), EpisodeState.UNPLAYED),
        (_episode(1, 1, on_disk=False, premiere="2027-01-01T00:00:00Z"), EpisodeState.UNAIRED),
        (_episode(1, 1, on_disk=False, premiere=None), EpisodeState.UNAIRED),
    ],
)
def test_episode_state(raw: dict, state: EpisodeState) -> None:
    assert EpisodeProgress.from_api(raw, TODAY).state is state


def test_virtual_episodes_are_not_on_disk_and_have_no_size() -> None:
    episode = EpisodeProgress.from_api(_episode(2, 3, on_disk=False, played=True), TODAY)

    assert not episode.on_disk
    assert episode.size == 0
    assert episode.code == "S02E03"


def test_seasons_count_aired_episodes_and_on_disk_progress_separately() -> None:
    episodes = [
        EpisodeProgress.from_api(raw, TODAY)
        for raw in [
            _episode(1, 1, on_disk=False, played=True, last_played="2025-08-14T00:00:00Z"),
            _episode(1, 2, on_disk=False),
            _episode(2, 1, played=True, last_played="2026-11-02T00:00:00Z", size=2 * GIB),
            _episode(2, 2, size=3 * GIB),
            _episode(2, 3, on_disk=False, premiere="2027-01-01T00:00:00Z"),
        ]
    ]

    first, second = summarise_seasons(episodes)

    assert (first.season, first.aired, first.played, first.on_disk) == (1, 2, 1, 0)
    assert first.last_played == dt.date(2025, 8, 14)
    assert (second.aired, second.played, second.on_disk, second.on_disk_played) == (2, 1, 2, 1)
    assert second.size == 5 * GIB


def _server(monkeypatch: pytest.MonkeyPatch, series: list[dict], episodes: list[dict]) -> list[Report]:
    monkeypatch.setattr(progress, "build_headers", lambda _token: {})
    monkeypatch.setattr(progress, "get_users", lambda _url, _headers: [{"Name": "isobel", "Id": "i"}])
    monkeypatch.setattr(progress, "find_series", lambda _url, _headers, _uid, _search: series)
    monkeypatch.setattr(progress, "get_series_episodes", lambda _url, _headers, _sid, _uid: episodes)
    reports: list[Report] = []
    monkeypatch.setattr(progress, "emit", lambda report, _fmt: reports.append(report))
    return reports


def _run(show: str = "Slow Horses", *, include_specials: bool = False, all_episodes: bool = False) -> None:
    progress.run_progress(
        "isobel",
        show,
        "http://jf",
        "token",
        OutputFormat.JSON,
        today=TODAY,
        include_specials=include_specials,
        all_episodes=all_episodes,
    )


def test_report_separates_watched_on_disk_from_watched_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    episodes = [
        _episode(0, 1, on_disk=False),
        _episode(1, 1, on_disk=False),
        _episode(2, 1, played=True),
        _episode(2, 2, played=True),
    ]
    reports = _server(monkeypatch, [{"Id": "s1", "Name": "Slow Horses"}], episodes)

    _run()

    payload = reports[0].payload
    assert payload["verdict"] == "Watched every episode on disk, but not every aired episode."
    assert (payload["aired"], payload["played"], payload["on_disk"], payload["on_disk_played"]) == (
        3,
        2,
        2,
        2,
    )
    assert [episode["code"] for episode in payload["episodes"]] == ["S01E01", "S02E01", "S02E02"]
    not_played = reports[0].tables[1]
    assert [row[0] for row in not_played.rows] == ["S01E01"]


def test_specials_are_counted_on_request(monkeypatch: pytest.MonkeyPatch) -> None:
    reports = _server(
        monkeypatch, [{"Id": "s1", "Name": "Show"}], [_episode(0, 1), _episode(1, 1, played=True)]
    )

    _run("Show", include_specials=True)

    assert reports[0].payload["aired"] == 2
    assert reports[0].payload["verdict"] == "Partway through."


def test_all_episodes_lists_played_ones_too(monkeypatch: pytest.MonkeyPatch) -> None:
    reports = _server(monkeypatch, [{"Id": "s1", "Name": "Show"}], [_episode(1, 1, played=True)])

    _run("Show", all_episodes=True)

    assert reports[0].payload["verdict"] == "Watched every aired episode."
    assert reports[0].tables[1].title == "Episodes"
    assert [row[2] for row in reports[0].tables[1].rows] == ["played"]


def test_an_exact_title_wins_over_other_search_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    series = [{"Id": "s1", "Name": "Youth"}, {"Id": "s2", "Name": "Youth Club"}]
    reports = _server(monkeypatch, series, [])

    _run("youth")

    assert reports[0].payload["series"] == {"id": "s1", "name": "Youth"}
    assert reports[0].payload["verdict"] == "Not started."


def test_an_ambiguous_search_lists_the_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    _server(monkeypatch, [{"Id": "s1", "Name": "Crew Girl"}, {"Id": "s2", "Name": "Skeleton Crew"}], [])

    with pytest.raises(click.UsageError, match="several series: Crew Girl, Skeleton Crew"):
        _run("crew")


def test_no_match_and_unknown_user_are_usage_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    _server(monkeypatch, [], [])

    with pytest.raises(click.UsageError, match="No series matches"):
        _run("nothing")
    with pytest.raises(click.UsageError, match=r"No such user: bob\. Available on this server: isobel\."):
        progress.run_progress("bob", "x", "http://jf", "token", OutputFormat.JSON)
