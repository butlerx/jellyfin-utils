"""``user progress`` — how far one user is through a series, season by season."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

import click

from jellyfin_utils.client import build_headers, find_series, get_series_episodes, get_users, size_gb
from jellyfin_utils.output import OutputFormat, Report, Table, emit

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["EpisodeProgress", "EpisodeState", "run_progress", "summarise_seasons"]

SPECIALS_SEASON = 0


class EpisodeState(StrEnum):
    """Where one user stands on one episode."""

    PLAYED = "played"
    STARTED = "started"
    """Has a last-played date but is not marked played: abandoned, or watched somewhere untracked."""
    UNPLAYED = "unplayed"
    UNAIRED = "unaired"


@dataclass(frozen=True)
class EpisodeProgress:
    """One episode's identity, file state, and one user's watch state."""

    season: int | None
    number: int | None
    title: str
    on_disk: bool
    size: int
    state: EpisodeState
    last_played: dt.date | None

    @property
    def code(self) -> str:
        """``S01E02`` style label, with ``?`` for missing numbers."""
        season = f"{self.season:02d}" if self.season is not None else "??"
        number = f"{self.number:02d}" if self.number is not None else "??"
        return f"S{season}E{number}"

    @classmethod
    def from_api(cls, raw: dict, today: dt.date) -> EpisodeProgress:
        """Build from a ``/Shows/{id}/Episodes`` record that carries ``UserData``."""
        user_data = raw.get("UserData") or {}
        last_played = _parse_date(user_data.get("LastPlayedDate"))
        premiere = _parse_date(raw.get("PremiereDate"))
        on_disk = raw.get("LocationType") != "Virtual" and bool(raw.get("Path"))
        if user_data.get("Played"):
            state = EpisodeState.PLAYED
        elif last_played or user_data.get("PlaybackPositionTicks"):
            state = EpisodeState.STARTED
        elif not on_disk and (premiere is None or premiere > today):
            state = EpisodeState.UNAIRED
        else:
            state = EpisodeState.UNPLAYED
        sources = raw.get("MediaSources") or []
        return cls(
            season=raw.get("ParentIndexNumber"),
            number=raw.get("IndexNumber"),
            title=raw.get("Name", "<no name>"),
            on_disk=on_disk,
            size=sum(source.get("Size", 0) or 0 for source in sources[:1]) if on_disk else 0,
            state=state,
            last_played=last_played,
        )


@dataclass(frozen=True)
class SeasonSummary:
    """Totals for one season, counting aired episodes only."""

    season: int | None
    aired: int
    played: int
    on_disk: int
    on_disk_played: int
    size: int
    last_played: dt.date | None


def _parse_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value).date()
    except ValueError:
        return None


def summarise_seasons(episodes: Sequence[EpisodeProgress]) -> list[SeasonSummary]:
    """Roll episodes up into one summary per season, in season order."""
    seasons: dict[int | None, list[EpisodeProgress]] = {}
    for episode in episodes:
        seasons.setdefault(episode.season, []).append(episode)
    summaries = []
    for season in sorted(seasons, key=lambda value: -1 if value is None else value):
        aired = [episode for episode in seasons[season] if episode.state is not EpisodeState.UNAIRED]
        on_disk = [episode for episode in aired if episode.on_disk]
        dates = [episode.last_played for episode in aired if episode.last_played]
        summaries.append(
            SeasonSummary(
                season=season,
                aired=len(aired),
                played=sum(episode.state is EpisodeState.PLAYED for episode in aired),
                on_disk=len(on_disk),
                on_disk_played=sum(episode.state is EpisodeState.PLAYED for episode in on_disk),
                size=sum(episode.size for episode in on_disk),
                last_played=max(dates, default=None),
            )
        )
    return summaries


def _verdict(aired: Sequence[EpisodeProgress]) -> str:
    played = sum(episode.state is EpisodeState.PLAYED for episode in aired)
    on_disk = [episode for episode in aired if episode.on_disk]
    if aired and played == len(aired):
        return "Watched every aired episode."
    if on_disk and all(episode.state is EpisodeState.PLAYED for episode in on_disk):
        return "Watched every episode on disk, but not every aired episode."
    if not played:
        return "Not started."
    return "Partway through."


def _resolve_user_id(users: list[dict], username: str) -> str:
    for user in users:
        if user.get("Name") == username and user.get("Id"):
            return user["Id"]
    available = ", ".join(sorted(user["Name"] for user in users if user.get("Name") and user.get("Id")))
    message = f"No such user: {username}. Available on this server: {available or 'none'}."
    raise click.UsageError(message)


def _pick_series(matches: list[dict], show: str) -> dict:
    """Prefer an exact (case-insensitive) title match; otherwise require a single result."""
    if not matches:
        message = f"No series matches {show!r}."
        raise click.UsageError(message)
    exact = [match for match in matches if (match.get("Name") or "").casefold() == show.casefold()]
    if len(exact) == 1:
        return exact[0]
    if len(matches) == 1:
        return matches[0]
    names = ", ".join(sorted(match.get("Name") or "<no name>" for match in matches))
    message = f"{show!r} matches several series: {names}. Use the full title."
    raise click.UsageError(message)


def _date(value: dt.date | None) -> str:
    return value.isoformat() if value else "—"


def _season_label(season: int | None) -> str:
    if season is None:
        return "?"
    return "Specials" if season == SPECIALS_SEASON else str(season)


def run_progress(
    username: str,
    show: str,
    base_url: str,
    token: str,
    output_format: OutputFormat,
    *,
    include_specials: bool = False,
    all_episodes: bool = False,
    today: dt.date | None = None,
) -> None:
    """
    Report USERNAME's progress through SHOW, season by season.

    Counts cover aired episodes, including ones no longer (or never) on disk, so "every
    episode on disk is played" and "watched the whole show" stay distinguishable. Specials
    are left out unless ``include_specials`` is set.
    """
    today = today or dt.datetime.now(dt.UTC).date()
    headers = build_headers(token)
    user_id = _resolve_user_id(get_users(base_url, headers), username)
    series = _pick_series(find_series(base_url, headers, user_id, show), show)
    episodes = [
        EpisodeProgress.from_api(raw, today)
        for raw in get_series_episodes(base_url, headers, series["Id"], user_id)
    ]
    if not include_specials:
        episodes = [episode for episode in episodes if episode.season != SPECIALS_SEASON]

    aired = [episode for episode in episodes if episode.state is not EpisodeState.UNAIRED]
    seasons = summarise_seasons(episodes)
    played = sum(episode.state is EpisodeState.PLAYED for episode in aired)
    on_disk = [episode for episode in aired if episode.on_disk]
    listed = (
        episodes
        if all_episodes
        else [episode for episode in aired if episode.state is not EpisodeState.PLAYED]
    )
    unaired = len(episodes) - len(aired)
    on_disk_size = sum(episode.size for episode in on_disk)
    on_disk_played = sum(episode.state is EpisodeState.PLAYED for episode in on_disk)

    season_rows = [
        (
            _season_label(summary.season),
            f"{summary.played}/{summary.aired}",
            f"{summary.on_disk_played}/{summary.on_disk}",
            f"{size_gb(summary.size):.2f} GiB",
            _date(summary.last_played),
        )
        for summary in seasons
        if summary.aired
    ]
    episode_rows = [
        (
            episode.code,
            episode.title,
            episode.state.value,
            "yes" if episode.on_disk else "no",
            _date(episode.last_played),
        )
        for episode in listed
    ]
    notes = []
    if any(episode.state is EpisodeState.STARTED for episode in aired):
        notes.append(
            "started = has a last-played date but is not marked played: stopped early, or "
            "watched somewhere Jellyfin did not track."
        )
    if unaired:
        notes.append(f"{unaired} unaired episode(s) not counted.")

    emit(
        Report(
            title=f"{series.get('Name', show)} — {username}",
            payload={
                "user": username,
                "series": {"id": series["Id"], "name": series.get("Name")},
                "verdict": _verdict(aired),
                "aired": len(aired),
                "played": played,
                "on_disk": len(on_disk),
                "on_disk_played": on_disk_played,
                "on_disk_size": on_disk_size,
                "unaired": unaired,
                "seasons": [
                    {
                        "season": summary.season,
                        "aired": summary.aired,
                        "played": summary.played,
                        "on_disk": summary.on_disk,
                        "on_disk_played": summary.on_disk_played,
                        "size": summary.size,
                        "last_played": _date(summary.last_played) if summary.last_played else None,
                    }
                    for summary in seasons
                ],
                "episodes": [
                    {
                        "code": episode.code,
                        "season": episode.season,
                        "number": episode.number,
                        "title": episode.title,
                        "state": episode.state.value,
                        "on_disk": episode.on_disk,
                        "size": episode.size,
                        "last_played": episode.last_played.isoformat() if episode.last_played else None,
                    }
                    for episode in episodes
                ],
            },
            summary=[
                ("Verdict", _verdict(aired)),
                ("Played", f"{played} of {len(aired)} aired episodes"),
                (
                    "On disk",
                    f"{len(on_disk)} episodes ({size_gb(on_disk_size):.2f} GiB), {on_disk_played} played",
                ),
            ],
            notes=notes,
            tables=[
                Table(
                    columns=("Season", "Played", "On disk played", "Size", "Last played"),
                    rows=season_rows,
                    title="Seasons",
                    align="lrrrl",
                ),
                Table(
                    columns=("Episode", "Title", "State", "On disk", "Last played"),
                    rows=episode_rows,
                    title="Episodes" if all_episodes else "Not played",
                    empty="Every aired episode is played.",
                ),
            ],
        ),
        output_format,
    )
