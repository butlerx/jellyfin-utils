"""Payload and report rendering for user clone operations."""

from __future__ import annotations

from typing import TYPE_CHECKING

import orjson

from jellyfin_utils.output import OutputFormat, Report, Table, emit

if TYPE_CHECKING:
    import pathlib

    from jellyfin_utils.user.models import CloneUserOutcome, ItemDifference, VerificationResult

DETAIL_LIMIT = 25


def item_difference_payload(item: ItemDifference) -> dict[str, object]:
    """Build the stable JSON-compatible payload for one item difference."""
    return {
        "id": item.item_id,
        "name": item.name,
        "type": item.item_type,
        "fields": list(item.fields),
        "expected": dict(item.expected),
        "actual": dict(item.actual),
    }


def verification_payload(
    verification: VerificationResult,
    detail_limit: int | None = DETAIL_LIMIT,
) -> dict[str, object]:
    """Build the stable verification payload, optionally limiting item details."""
    missing_items = (
        verification.missing_items[:detail_limit] if detail_limit is not None else verification.missing_items
    )
    mismatched_items = (
        verification.mismatched_items[:detail_limit]
        if detail_limit is not None
        else verification.mismatched_items
    )
    duplicate_ids = (
        verification.duplicate_source_item_ids[:detail_limit]
        if detail_limit is not None
        else verification.duplicate_source_item_ids
    )
    total_details = (
        len(verification.missing_items)
        + len(verification.mismatched_items)
        + len(verification.duplicate_source_item_ids)
    )
    returned_details = len(missing_items) + len(mismatched_items) + len(duplicate_ids)
    return {
        "verified": verification.verified,
        "user_data_expected": verification.user_data_expected,
        "user_data_matched": verification.user_data_matched,
        "watch_items_expected": verification.watch_items_expected,
        "watch_items_matched": verification.watch_items_matched,
        "favorites_expected": verification.favorites_expected,
        "favorites_matched": verification.favorites_matched,
        "playlists_expected": verification.playlists_expected,
        "playlists_matched": verification.playlists_matched,
        "direct_item_checks": verification.direct_item_checks,
        "direct_item_matches": verification.direct_item_matches,
        "source_duplicate_rows_removed": verification.duplicate_source_rows_removed,
        "source_duplicate_item_count": len(verification.duplicate_source_item_ids),
        "source_duplicate_item_ids": list(duplicate_ids),
        "missing_item_count": len(verification.missing_items),
        "missing_item_ids": [item.item_id for item in missing_items],
        "missing_items": [item_difference_payload(item) for item in missing_items],
        "mismatched_item_count": len(verification.mismatched_items),
        "mismatched_item_ids": [item.item_id for item in mismatched_items],
        "mismatched_items": [item_difference_payload(item) for item in mismatched_items],
        "missing_playlists": list(verification.missing_playlists),
        "details_truncated": returned_details < total_details,
    }


def clone_payload(outcome: CloneUserOutcome) -> dict[str, object]:
    """Build the stable successful clone payload."""
    if outcome.jellyseerr_user_id is None:
        message = "Cannot build a clone payload before Jellyseerr setup completes."
        raise ValueError(message)
    action = "Cloned" if outcome.destination_created else "Resumed and verified clone of"
    title = f'{action} Jellyfin user "{outcome.source_name}" to "{outcome.destination_name}".'
    return {
        "created": outcome.destination_created,
        "resumed": not outcome.destination_created,
        "source_username": outcome.source_name,
        "username": outcome.destination_name,
        "email": outcome.email,
        "jellyfin_user_id": outcome.jellyfin_user_id,
        "jellyseerr_user_id": outcome.jellyseerr_user_id,
        "watch_items_copied": outcome.counts.watch_items,
        "favorites_copied": outcome.counts.favorites,
        "playlists_copied": outcome.counts.playlists,
        "playlist_items_copied": outcome.counts.playlist_items,
        "user_data_updated": outcome.counts.user_data_updated,
        "user_data_reused": outcome.counts.user_data_reused,
        "playlists_created": outcome.counts.playlists_created,
        "playlists_reused": outcome.counts.playlists_reused,
        "jellyseerr_requests_copied": 0,
        "password_set": outcome.password_set,
        **verification_payload(outcome.verification),
        "message": title,
    }


def _difference_detail(item: ItemDifference) -> str:
    expected = dict(item.expected)
    if not item.fields:
        values = "; ".join(f"{field}: {value!r}" for field, value in item.expected)
        return f"record absent; expected {values}"
    actual = dict(item.actual)
    return "; ".join(f"{field}: {expected[field]!r} → {actual[field]!r}" for field in item.fields)


def verification_tables(verification: VerificationResult) -> tuple[Table, ...]:
    """Build human-readable item-difference tables."""
    differences = [
        ("Missing", item.item_type, item.name, item.item_id, _difference_detail(item))
        for item in verification.missing_items
    ]
    differences.extend(
        ("Mismatch", item.item_type, item.name, item.item_id, _difference_detail(item))
        for item in verification.mismatched_items
    )
    if not differences:
        return ()
    return (
        Table(
            title="Item differences",
            columns=("Status", "Type", "Name", "Item ID", "Details"),
            rows=differences[:DETAIL_LIMIT],
        ),
    )


def write_verification_details(
    path: pathlib.Path,
    source_name: str,
    destination_name: str,
    verification: VerificationResult,
) -> None:
    """Serialize complete verification differences to the established JSON format."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source_username": source_name,
        "destination_username": destination_name,
        **verification_payload(verification, detail_limit=None),
    }
    path.write_bytes(orjson.dumps(payload, option=orjson.OPT_INDENT_2) + b"\n")


def emit_verification_report(
    source_name: str,
    destination_name: str,
    verification: VerificationResult,
    output_format: OutputFormat,
    details_file: pathlib.Path | None = None,
) -> None:
    """Render a verification result and optionally persist complete details."""
    if details_file is not None:
        write_verification_details(details_file, source_name, destination_name, verification)
    status = "verified" if verification.verified else "differs"
    title = f'Clone {status}: "{source_name}" → "{destination_name}".'
    notes = []
    if not verification.verified:
        notes.append(
            "Destination state was reread from Jellyfin; repeating the unchanged clone is unlikely to help."
        )
    item_difference_count = len(verification.missing_items) + len(verification.mismatched_items)
    if item_difference_count > DETAIL_LIMIT and details_file is None:
        notes.append(
            f"Showing {DETAIL_LIMIT}/{item_difference_count} item differences; "
            "use --details-file PATH to save all details."
        )
    if details_file is not None:
        notes.append(f"Full difference details written to {details_file}.")
    emit(
        Report(
            title=title,
            payload={
                "source_username": source_name,
                "destination_username": destination_name,
                **verification_payload(verification),
                "details_file": str(details_file) if details_file is not None else None,
                "message": title,
            },
            summary=(
                ("Verified", verification.verified),
                ("Watch state", f"{verification.watch_items_matched}/{verification.watch_items_expected}"),
                ("Favorites", f"{verification.favorites_matched}/{verification.favorites_expected}"),
                ("Playlists", f"{verification.playlists_matched}/{verification.playlists_expected}"),
                ("Direct item checks", verification.direct_item_checks),
                ("Direct matches", verification.direct_item_matches),
                ("Duplicate source rows removed", verification.duplicate_source_rows_removed),
                ("Missing item records", len(verification.missing_items)),
                ("Mismatched item records", len(verification.mismatched_items)),
            ),
            tables=verification_tables(verification),
            notes=notes,
        ),
        output_format,
    )


def emit_clone_report(outcome: CloneUserOutcome, output_format: OutputFormat) -> None:
    """Render a successfully verified clone outcome."""
    payload = clone_payload(outcome)
    title = str(payload["message"])
    notes = ["Jellyseerr request ownership was left unchanged."]
    if not outcome.destination_created:
        notes.append("The existing destination password was left unchanged.")
    emit(
        Report(
            title=title,
            payload=payload,
            summary=(
                ("Verified", outcome.verification.verified),
                ("Destination created", outcome.destination_created),
                ("Jellyfin ID", outcome.jellyfin_user_id),
                ("Jellyseerr ID", outcome.jellyseerr_user_id),
                ("Email", outcome.email),
                (
                    "Watch state",
                    f"{outcome.verification.watch_items_matched}/{outcome.verification.watch_items_expected}",
                ),
                (
                    "Favorites",
                    f"{outcome.verification.favorites_matched}/{outcome.verification.favorites_expected}",
                ),
                (
                    "Playlists",
                    f"{outcome.verification.playlists_matched}/{outcome.verification.playlists_expected}",
                ),
                ("User-data records updated", outcome.counts.user_data_updated),
                ("User-data records reused", outcome.counts.user_data_reused),
                ("Playlists created", outcome.counts.playlists_created),
                ("Playlists reused", outcome.counts.playlists_reused),
                ("Password set on create", outcome.password_set),
            ),
            notes=tuple(notes),
        ),
        output_format,
    )


# Private aliases preserve imports used before rendering moved out of the CLI module.
_item_difference_payload = item_difference_payload
_verification_payload = verification_payload
_verification_tables = verification_tables
_write_verification_details = write_verification_details
_emit_verification_report = emit_verification_report
_emit_clone_report = emit_clone_report
