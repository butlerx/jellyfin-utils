"""``user watched`` — list one or more users' watched movies and finished series, by size."""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

from jellyfin_utils.client import build_headers, get_all_items, get_users, get_watched_item_ids, size_gb
from jellyfin_utils.output import OutputFormat, Report, Table, emit

if TYPE_CHECKING:
    from jellyfin_utils.client import LibraryItem

__all__ = ["run_watched"]

# Above this many unsized items, naming every title in a note stops being useful and just
# becomes noise (some libraries carry hundreds of extras/specials with no real media file).
MAX_UNSIZED_NAMES_IN_NOTE = 10


def _resolve_user_ids(users: list[dict], usernames: tuple[str, ...]) -> dict[str, str]:
    """Map each requested username to its Jellyfin user ID, or fail with the real options."""
    by_name = {user["Name"]: user["Id"] for user in users if user.get("Name") and user.get("Id")}
    missing = [name for name in usernames if name not in by_name]
    if missing:
        available = ", ".join(sorted(by_name)) or "none"
        message = f"No such user(s): {', '.join(missing)}. Available on this server: {available}."
        raise click.UsageError(message)
    return {name: by_name[name] for name in usernames}


def _watched_entries(
    watched_ids: set[str], by_id: dict[str, LibraryItem]
) -> tuple[list[LibraryItem], list[LibraryItem]]:
    """
    Split watched items present in the library into (sized, unsized), largest first.

    ``size == 0`` covers two very different situations this command cannot tell apart from
    the API alone: an item genuinely has no on-disk media left (e.g. a series whose real
    episodes were deleted but an "Inside the Episodes" extra stub remains), or its size could
    not be determined. Either way it does not belong in a size-ranked table, so it is reported
    as a count/note instead of a misleadingly-placed row.
    """
    matched = [item for item_id, item in by_id.items() if item_id in watched_ids]
    sized = sorted((item for item in matched if item.size), key=lambda item: -item.size)
    unsized = [item for item in matched if not item.size]
    return sized, unsized


def _table(title: str, entries: list[LibraryItem], *, empty: str) -> Table:
    rows = [(item.name, f"{size_gb(item.size):.2f} GiB") for item in entries]
    return Table(columns=("Title", "Size"), rows=rows, title=title, align="lr", empty=empty)


def _unsized_note(label: str, unsized: list[LibraryItem]) -> str | None:
    if not unsized:
        return None
    if len(unsized) <= MAX_UNSIZED_NAMES_IN_NOTE:
        names = ", ".join(item.name for item in unsized)
        return f"{label}: {len(unsized)} watched with no on-disk size — {names}."
    return f"{label}: {len(unsized)} watched with no on-disk size (list omitted, too many to show)."


def run_watched(usernames: tuple[str, ...], base_url: str, token: str, output_format: OutputFormat) -> None:
    """
    List USERNAMES' watched movies and fully-watched series, largest on-disk size first.

    A series only counts as finished when it still has episodes in the library — a series
    Jellyfin reports as "played" purely because every remaining episode (zero of them) has
    been watched is excluded, not listed as finished.
    """
    headers = build_headers(token)
    users = get_users(base_url, headers)
    user_ids = _resolve_user_ids(users, usernames)

    # get_all_items() already drops episode-less series, so a series that only looks
    # "finished" because it has no episodes left never appears in series_by_id at all.
    items = get_all_items(base_url, headers)
    movies_by_id = {item.item_id: item for item in items if item.item_type == "Movie"}
    series_by_id = {item.item_id: item for item in items if item.item_type == "Series"}

    users_payload: dict[str, dict[str, object]] = {}
    tables: list[Table] = []
    notes: list[str] = []
    for name, user_id in user_ids.items():
        sized_movies, unsized_movies = _watched_entries(
            get_watched_item_ids(base_url, headers, user_id, "Movie"), movies_by_id
        )
        sized_series, unsized_series = _watched_entries(
            get_watched_item_ids(base_url, headers, user_id, "Series"), series_by_id
        )
        users_payload[name] = {
            "movies": {
                "sized": [{"name": item.name, "size": item.size} for item in sized_movies],
                "unsized_count": len(unsized_movies),
                "unsized_names": [item.name for item in unsized_movies],
            },
            "series": {
                "sized": [{"name": item.name, "size": item.size} for item in sized_series],
                "unsized_count": len(unsized_series),
                "unsized_names": [item.name for item in unsized_series],
            },
        }
        movie_total = len(sized_movies) + len(unsized_movies)
        series_total = len(sized_series) + len(unsized_series)
        tables.append(
            _table(f"{name} — Movies ({movie_total} watched)", sized_movies, empty="Nothing watched.")
        )
        tables.append(
            _table(f"{name} — Series ({series_total} finished)", sized_series, empty="Nothing finished.")
        )
        notes.extend(
            note
            for note in (
                _unsized_note(f"{name} movies", unsized_movies),
                _unsized_note(f"{name} series", unsized_series),
            )
            if note
        )

    emit(
        Report(
            title="Watched media by user",
            payload={"users": users_payload},
            summary=[("Users", ", ".join(user_ids))],
            notes=notes,
            tables=tables,
        ),
        output_format,
    )
