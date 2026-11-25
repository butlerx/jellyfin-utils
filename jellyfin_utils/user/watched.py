"""``user watched`` — list one or more users' watched movies and finished series, by size."""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

from jellyfin_utils.client import build_headers, get_all_items, get_users, get_watched_item_ids, size_gb
from jellyfin_utils.output import OutputFormat, Report, Table, emit

if TYPE_CHECKING:
    from collections.abc import Mapping

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


def _requested_by(item: LibraryItem, requesters: Mapping[int, tuple[str, ...]] | None) -> tuple[str, ...]:
    if requesters is None or item.tmdb_id is None:
        return ()
    return requesters.get(item.tmdb_id, ())


def _keep_requested(
    watched_ids: set[str],
    by_id: dict[str, LibraryItem],
    requesters: Mapping[int, tuple[str, ...]],
    usernames: tuple[str, ...],
) -> set[str]:
    """Keep only the watched IDs that one of USERNAMES requested in Jellyseerr."""
    wanted = {name.casefold() for name in usernames}
    return {
        item_id
        for item_id in watched_ids
        if (item := by_id.get(item_id)) is not None
        and any(name.casefold() in wanted for name in _requested_by(item, requesters))
    }


def _entries_payload(
    entries: list[LibraryItem], requesters: Mapping[int, tuple[str, ...]] | None
) -> list[dict[str, object]]:
    payload: list[dict[str, object]] = []
    for item in entries:
        entry: dict[str, object] = {"name": item.name, "size": item.size}
        if requesters is not None:
            entry["requested_by"] = list(_requested_by(item, requesters))
        payload.append(entry)
    return payload


def _table(
    title: str,
    entries: list[LibraryItem],
    *,
    empty: str,
    requesters: Mapping[int, tuple[str, ...]] | None = None,
) -> Table:
    if requesters is None:
        rows = [(item.name, f"{size_gb(item.size):.2f} GiB") for item in entries]
        return Table(columns=("Title", "Size"), rows=rows, title=title, align="lr", empty=empty)
    rows = [
        (item.name, ", ".join(_requested_by(item, requesters)) or "—", f"{size_gb(item.size):.2f} GiB")
        for item in entries
    ]
    return Table(columns=("Title", "Requested by", "Size"), rows=rows, title=title, align="llr", empty=empty)


def _unsized_note(label: str, unsized: list[LibraryItem]) -> str | None:
    if not unsized:
        return None
    if len(unsized) <= MAX_UNSIZED_NAMES_IN_NOTE:
        names = ", ".join(item.name for item in unsized)
        return f"{label}: {len(unsized)} watched with no on-disk size — {names}."
    return f"{label}: {len(unsized)} watched with no on-disk size (list omitted, too many to show)."


def _section_payload(
    sized: list[LibraryItem],
    unsized: list[LibraryItem],
    requesters: Mapping[int, tuple[str, ...]] | None,
) -> dict[str, object]:
    return {
        "sized": _entries_payload(sized, requesters),
        "unsized_count": len(unsized),
        "unsized_names": [item.name for item in unsized],
    }


def run_watched(
    usernames: tuple[str, ...],
    base_url: str,
    token: str,
    output_format: OutputFormat,
    *,
    together: bool = False,
    requested: bool = False,
    requesters_by_tmdb_id: Mapping[int, tuple[str, ...]] | None = None,
) -> None:
    """
    List USERNAMES' watched movies and fully-watched series, largest on-disk size first.

    A series only counts as finished when it still has episodes in the library — a series
    Jellyfin reports as "played" purely because every remaining episode (zero of them) has
    been watched is excluded, not listed as finished.

    ``together`` reports one list of titles every user has watched instead of one per user.
    ``requested`` keeps only titles the user (or, with ``together``, any of the users)
    requested in Jellyseerr, so it needs ``requesters_by_tmdb_id``. Passing requesters also
    adds a "Requested by" column.
    """
    if together and len(set(usernames)) < 2:  # noqa: PLR2004 - "together" needs a second user
        message = "--together needs at least two usernames."
        raise click.UsageError(message)
    if requested and requesters_by_tmdb_id is None:
        message = "--requested needs Jellyseerr; set --jellyseerr-server and --jellyseerr-token."
        raise click.UsageError(message)

    headers = build_headers(token)
    users = get_users(base_url, headers)
    user_ids = _resolve_user_ids(users, usernames)

    # get_all_items() already drops episode-less series, so a series that only looks
    # "finished" because it has no episodes left never appears in series_by_id at all.
    items = get_all_items(base_url, headers)
    by_type = {
        "Movie": {item.item_id: item for item in items if item.item_type == "Movie"},
        "Series": {item.item_id: item for item in items if item.item_type == "Series"},
    }

    watched_ids: dict[str, dict[str, set[str]]] = {
        name: {
            item_type: get_watched_item_ids(base_url, headers, user_id, item_type) for item_type in by_type
        }
        for name, user_id in user_ids.items()
    }
    if together:
        group = ", ".join(user_ids)
        watched_ids = {
            group: {
                item_type: set.intersection(*(ids[item_type] for ids in watched_ids.values()))
                for item_type in by_type
            }
        }
    if requested and requesters_by_tmdb_id is not None:
        requesters = requesters_by_tmdb_id
        watched_ids = {
            label: {
                item_type: _keep_requested(
                    ids[item_type], by_type[item_type], requesters, tuple(user_ids) if together else (label,)
                )
                for item_type in by_type
            }
            for label, ids in watched_ids.items()
        }

    payload_sections: dict[str, dict[str, object]] = {}
    tables: list[Table] = []
    notes: list[str] = []
    for label, ids in watched_ids.items():
        sized_movies, unsized_movies = _watched_entries(ids["Movie"], by_type["Movie"])
        sized_series, unsized_series = _watched_entries(ids["Series"], by_type["Series"])
        payload_sections[label] = {
            "movies": _section_payload(sized_movies, unsized_movies, requesters_by_tmdb_id),
            "series": _section_payload(sized_series, unsized_series, requesters_by_tmdb_id),
        }
        heading = f"Watched by all of {label}" if together else label
        movie_total = len(sized_movies) + len(unsized_movies)
        series_total = len(sized_series) + len(unsized_series)
        tables.append(
            _table(
                f"{heading} — Movies ({movie_total} watched)",
                sized_movies,
                empty="Nothing watched.",
                requesters=requesters_by_tmdb_id,
            )
        )
        tables.append(
            _table(
                f"{heading} — Series ({series_total} finished)",
                sized_series,
                empty="Nothing finished.",
                requesters=requesters_by_tmdb_id,
            )
        )
        notes.extend(
            note
            for note in (
                _unsized_note(f"{heading} movies", unsized_movies),
                _unsized_note(f"{heading} series", unsized_series),
            )
            if note
        )

    payload: dict[str, object] = {"filters": {"together": together, "requested": requested}}
    if together:
        payload["together"] = {"users": list(user_ids), **next(iter(payload_sections.values()))}
    else:
        payload["users"] = payload_sections
    summary: list[tuple[str, object]] = [("Users", ", ".join(user_ids))]
    if together:
        summary.append(("Showing", "titles every user has watched"))
    if requested:
        summary.append(
            ("Filter", "requested in Jellyseerr by " + ("any of these users" if together else "the user"))
        )

    emit(
        Report(
            title="Watched media by user",
            payload=payload,
            summary=summary,
            notes=notes,
            tables=tables,
        ),
        output_format,
    )
