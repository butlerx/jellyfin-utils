"""Pagination, series roll-up, and watch-history logic in the Jellyfin client."""

from __future__ import annotations

import datetime as dt

import pytest
import responses
from conftest import BASE_URL, items_page, make_item

from jellyfin_utils.client import (
    PAGE_SIZE,
    LibraryItem,
    drop_empty_series,
    get_all_items,
    get_watch_counts_per_item,
    get_watchers_per_item,
    iter_items,
    parse_last_played,
    roll_up_series_sizes,
)

ITEMS_URL = f"{BASE_URL}/Items"


def test_drop_empty_series_keeps_series_matched_by_id() -> None:
    items = [
        make_item("s1", "Series", name="Taskmaster"),
        make_item("e1", "Episode", series_id="s1"),
    ]
    assert [item.item_id for item in drop_empty_series(items)] == ["s1", "e1"]


def test_drop_empty_series_falls_back_to_a_case_insensitive_name_match() -> None:
    # Older Jellyfin records leave SeriesId unset, so the name is the only link.
    items = [
        make_item("s1", "Series", name="Taskmaster"),
        make_item("e1", "Episode", series_name="TASKMASTER"),
    ]
    assert len(drop_empty_series(items)) == 2


def test_drop_empty_series_removes_series_with_no_episodes() -> None:
    items = [
        make_item("s1", "Series", name="Gone"),
        make_item("s2", "Series", name="Kept"),
        make_item("e1", "Episode", series_id="s2"),
    ]
    assert [item.item_id for item in drop_empty_series(items)] == ["s2", "e1"]


def test_drop_empty_series_never_touches_movies() -> None:
    items = [make_item("m1", "Movie")]
    assert drop_empty_series(items) == items


def test_roll_up_series_sizes_totals_episodes_and_flags_the_rollup() -> None:
    items = [
        make_item("s1", "Series", name="Show", size=0),
        make_item("e1", "Episode", series_id="s1", size=100),
        make_item("e2", "Episode", series_id="s1", size=250),
    ]
    rolled = {item.item_id: item for item in roll_up_series_sizes(items)}
    assert rolled["s1"].size == 350
    assert rolled["s1"].size_is_rollup is True
    # Episodes keep their own size and stay un-flagged, so totals can skip only the parent.
    assert rolled["e1"].size == 100
    assert rolled["e1"].size_is_rollup is False


def test_roll_up_series_sizes_leaves_a_series_with_no_episodes_alone() -> None:
    items = [make_item("s1", "Series", name="Show", size=0)]
    rolled = roll_up_series_sizes(items)
    assert rolled[0].size == 0
    assert rolled[0].size_is_rollup is False


def test_parse_last_played_reads_user_data() -> None:
    item = {"UserData": {"LastPlayedDate": "2026-01-02T03:04:05+00:00"}}
    assert parse_last_played(item) == dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.UTC)


@pytest.mark.parametrize("item", [{}, {"UserData": None}, {"UserData": {}}])
def test_parse_last_played_returns_none_when_absent(item: dict) -> None:
    assert parse_last_played(item) is None


@responses.activate
def test_iter_items_walks_every_page(headers: dict[str, str]) -> None:
    first = [{"Id": f"a{index}"} for index in range(PAGE_SIZE)]
    responses.get(ITEMS_URL, json=items_page(first, total=PAGE_SIZE + 2))
    responses.get(ITEMS_URL, json=items_page([{"Id": "b0"}, {"Id": "b1"}], total=PAGE_SIZE + 2))

    collected = list(iter_items(BASE_URL, headers, {"Recursive": "true"}))

    assert len(collected) == PAGE_SIZE + 2
    assert collected[-1]["Id"] == "b1"
    assert len(responses.calls) == 2
    assert f"StartIndex={PAGE_SIZE}" in str(responses.calls[1].request.url)


@responses.activate
def test_iter_items_stops_on_an_empty_page(headers: dict[str, str]) -> None:
    # A server that under-reports TotalRecordCount must not loop forever.
    responses.get(ITEMS_URL, json=items_page([{"Id": "a"}], total=99))
    responses.get(ITEMS_URL, json=items_page([], total=99))

    assert len(list(iter_items(BASE_URL, headers, {}))) == 1
    assert len(responses.calls) == 2


@responses.activate
def test_iter_items_makes_one_call_when_the_first_page_is_the_whole_set(
    headers: dict[str, str],
) -> None:
    responses.get(ITEMS_URL, json=items_page([{"Id": "a"}, {"Id": "b"}], total=2))
    assert len(list(iter_items(BASE_URL, headers, {}))) == 2
    assert len(responses.calls) == 1


@responses.activate
def test_get_all_items_paginates_and_rolls_up_series(headers: dict[str, str]) -> None:
    page_one = [
        {"Id": "s1", "Name": "Show", "Type": "Series"},
        {"Id": "s2", "Name": "Empty", "Type": "Series"},
    ]
    page_two = [
        {
            "Id": "e1",
            "Name": "Ep 1",
            "Type": "Episode",
            "SeriesId": "s1",
            "MediaSources": [{"Size": 500}],
        },
    ]
    responses.get(ITEMS_URL, json=items_page(page_one, total=3))
    responses.get(ITEMS_URL, json=items_page(page_two, total=3))

    items = {item.item_id: item for item in get_all_items(BASE_URL, headers)}

    # The episode-less series is dropped even though it arrived on an earlier page.
    assert set(items) == {"s1", "e1"}
    assert items["s1"].size == 500
    assert items["s1"].size_is_rollup is True


@responses.activate
def test_get_all_items_skips_records_with_no_id(headers: dict[str, str]) -> None:
    responses.get(ITEMS_URL, json=items_page([{"Name": "orphan"}, {"Id": "m1"}], total=2))
    items = get_all_items(BASE_URL, headers, include_types="Movie")
    assert [item.item_id for item in items] == ["m1"]


@responses.activate
def test_get_all_items_skips_rollup_for_a_movie_only_query(headers: dict[str, str]) -> None:
    raw = {"Id": "m1", "Name": "Film", "Type": "Movie", "MediaSources": [{"Size": 10}]}
    responses.get(ITEMS_URL, json=items_page([raw], total=1))
    items = get_all_items(BASE_URL, headers, include_types="Movie")
    assert items == [
        LibraryItem(
            item_id="m1",
            name="Film",
            item_type="Movie",
            path="",
            size=10,
            series_name=None,
            series_id=None,
            parent_index=None,
            episode_index=None,
            date_created=None,
            tmdb_id=None,
            production_year=None,
        )
    ]


USERS = [
    {"Id": "u1", "Name": "alice"},
    {"Id": "u2", "Name": "bob"},
    {"Id": "u3", "Name": "service-account"},
    {"Name": "no-id"},
]


@responses.activate
def test_get_watchers_per_item_ignores_named_and_id_less_users(headers: dict[str, str]) -> None:
    responses.get(ITEMS_URL, json=items_page([{"Id": "m1"}], total=1))
    responses.get(ITEMS_URL, json=items_page([{"Id": "m1"}, {"Id": "m2"}], total=2))

    watchers = get_watchers_per_item(BASE_URL, headers, USERS, {"service-account"}, None)

    assert watchers == {"m1": ["alice", "bob"], "m2": ["bob"]}
    # One query per active user: the ignored name and the record with no Id are both skipped.
    assert len(responses.calls) == 2


@responses.activate
def test_get_watchers_per_item_applies_the_age_cutoff(headers: dict[str, str]) -> None:
    recent = (dt.datetime.now(dt.UTC) - dt.timedelta(days=2)).isoformat()
    stale = (dt.datetime.now(dt.UTC) - dt.timedelta(days=90)).isoformat()
    page = [
        {"Id": "fresh", "UserData": {"LastPlayedDate": recent}},
        {"Id": "old", "UserData": {"LastPlayedDate": stale}},
        {"Id": "never", "UserData": {}},
    ]
    responses.get(ITEMS_URL, json=items_page(page, total=3))

    watchers = get_watchers_per_item(BASE_URL, headers, [USERS[0]], set(), 30)

    assert watchers == {"fresh": ["alice"]}


@responses.activate
def test_get_watch_counts_per_item_counts_users(headers: dict[str, str]) -> None:
    responses.get(ITEMS_URL, json=items_page([{"Id": "m1"}], total=1))
    responses.get(ITEMS_URL, json=items_page([{"Id": "m1"}], total=1))

    counts = get_watch_counts_per_item(BASE_URL, headers, USERS[:2], set())

    assert counts == {"m1": 2}
