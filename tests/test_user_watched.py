"""``user watched`` report: user resolution, size ranking, and unsized notes."""

from __future__ import annotations

import click
import pytest
from conftest import make_item

from jellyfin_utils.output import OutputFormat, Report
from jellyfin_utils.user import watched

GIB = 1024**3


def _capture(monkeypatch: pytest.MonkeyPatch) -> list[Report]:
    reports: list[Report] = []
    monkeypatch.setattr(watched, "emit", lambda report, _fmt: reports.append(report))
    return reports


def _library(monkeypatch: pytest.MonkeyPatch, items: list, users: list[dict]) -> None:
    monkeypatch.setattr(watched, "build_headers", lambda _token: {})
    monkeypatch.setattr(watched, "get_users", lambda _url, _headers: users)
    monkeypatch.setattr(watched, "get_all_items", lambda _url, _headers: items)


def test_unknown_usernames_fail_with_the_available_users(monkeypatch: pytest.MonkeyPatch) -> None:
    _library(monkeypatch, [], [{"Name": "alice", "Id": "a"}, {"Name": "bob", "Id": "b"}])
    _capture(monkeypatch)

    with pytest.raises(click.UsageError) as caught:
        watched.run_watched(("alice", "carol"), "http://jf", "token", OutputFormat.TEXT)

    assert "No such user(s): carol" in caught.value.message
    assert "Available on this server: alice, bob." in caught.value.message


def test_users_without_a_name_or_id_cannot_be_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    _library(monkeypatch, [], [{"Name": "alice"}, {"Id": "b"}])
    _capture(monkeypatch)

    with pytest.raises(click.UsageError) as caught:
        watched.run_watched(("alice",), "http://jf", "token", OutputFormat.TEXT)

    assert "Available on this server: none." in caught.value.message


def test_sized_entries_are_largest_first_and_unsized_are_split_out(monkeypatch: pytest.MonkeyPatch) -> None:
    items = [
        make_item("m1", "Movie", name="Small", size=1 * GIB),
        make_item("m2", "Movie", name="Large", size=5 * GIB),
        make_item("m3", "Movie", name="Stub", size=0),
    ]
    _library(monkeypatch, items, [{"Name": "dana", "Id": "d"}])
    monkeypatch.setattr(watched, "get_watched_item_ids", lambda *_args: {"m1", "m2", "m3", "absent"})
    reports = _capture(monkeypatch)

    watched.run_watched(("dana",), "http://jf", "token", OutputFormat.JSON)

    movies = reports[0].payload["users"]["dana"]["movies"]
    assert [entry["name"] for entry in movies["sized"]] == ["Large", "Small"]
    assert movies["unsized_names"] == ["Stub"]


def test_report_ranks_each_users_titles_and_counts_finished_series(monkeypatch: pytest.MonkeyPatch) -> None:
    movie = make_item("m1", "Movie", name="Heat", size=2 * GIB)
    stub = make_item("m2", "Movie", name="Extra", size=0)
    show = make_item("s1", "Series", name="The Wire", size=10 * GIB)
    _library(monkeypatch, [movie, stub, show], [{"Name": "alice", "Id": "a"}])
    watched_by_type = {"Movie": {"m1", "m2"}, "Series": {"s1"}}
    monkeypatch.setattr(
        watched, "get_watched_item_ids", lambda _url, _headers, _uid, item_type: watched_by_type[item_type]
    )
    reports = _capture(monkeypatch)

    watched.run_watched(("alice",), "http://jf", "token", OutputFormat.TEXT)

    (report,) = reports
    assert report.summary == [("Users", "alice")]
    assert report.payload["users"]["alice"]["movies"] == {
        "sized": [{"name": "Heat", "size": 2 * GIB}],
        "unsized_count": 1,
        "unsized_names": ["Extra"],
    }
    assert report.payload["users"]["alice"]["series"] == {
        "sized": [{"name": "The Wire", "size": 10 * GIB}],
        "unsized_count": 0,
        "unsized_names": [],
    }
    titles = [table.title for table in report.tables]
    assert titles == ["alice — Movies (2 watched)", "alice — Series (1 finished)"]
    assert report.notes == ["alice movies: 1 watched with no on-disk size — Extra."]


def test_unsized_note_lists_names_up_to_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    extras = [
        make_item(f"e{i}", "Movie", name=f"Extra {i}", size=0)
        for i in range(watched.MAX_UNSIZED_NAMES_IN_NOTE)
    ]
    _library(monkeypatch, extras, [{"Name": "bob", "Id": "b"}])
    monkeypatch.setattr(watched, "get_watched_item_ids", lambda *_args: {item.item_id for item in extras})
    reports = _capture(monkeypatch)

    watched.run_watched(("bob",), "http://jf", "token", OutputFormat.JSON)

    (note,) = reports[0].notes
    assert note.startswith("bob movies: 10 watched with no on-disk size — Extra 0, Extra 1")


def test_unsized_note_omits_names_when_there_are_too_many(monkeypatch: pytest.MonkeyPatch) -> None:
    extras = [
        make_item(f"e{i}", "Movie", name=f"Extra {i}", size=0)
        for i in range(watched.MAX_UNSIZED_NAMES_IN_NOTE + 1)
    ]
    _library(monkeypatch, extras, [{"Name": "bob", "Id": "b"}])
    monkeypatch.setattr(watched, "get_watched_item_ids", lambda *_args: {item.item_id for item in extras})
    reports = _capture(monkeypatch)

    watched.run_watched(("bob",), "http://jf", "token", OutputFormat.JSON)

    assert reports[0].notes == [
        "bob movies: 11 watched with no on-disk size (list omitted, too many to show).",
    ]
    assert reports[0].payload["users"]["bob"]["movies"]["unsized_count"] == 11


def test_user_with_nothing_watched_has_no_notes(monkeypatch: pytest.MonkeyPatch) -> None:
    _library(monkeypatch, [make_item("m1", "Movie", size=GIB)], [{"Name": "carol", "Id": "c"}])
    monkeypatch.setattr(watched, "get_watched_item_ids", lambda *_args: set())
    reports = _capture(monkeypatch)

    watched.run_watched(("carol",), "http://jf", "token", OutputFormat.TEXT)

    assert reports[0].notes == []
    assert [table.title for table in reports[0].tables] == [
        "carol — Movies (0 watched)",
        "carol — Series (0 finished)",
    ]
