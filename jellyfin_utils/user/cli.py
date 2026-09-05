"""User-management commands for Jellyfin."""

from __future__ import annotations

import pathlib

import click
import orjson

from jellyfin_utils.client import build_headers, create_user, get_users
from jellyfin_utils.jellyseerr import (
    check_jellyseerr_user_management_access,
    find_linked_jellyseerr_user,
    get_jellyseerr_users,
    import_jellyfin_user,
    update_jellyseerr_profile,
)
from jellyfin_utils.options import connection_options, jellyseerr_options, output_option
from jellyfin_utils.output import OutputFormat, Report, Table, emit
from jellyfin_utils.user.service import (
    CloneCounts,
    ItemDifference,
    VerificationResult,
    apply_user_snapshot,
    capture_user_snapshot,
    resolve_destination_snapshot,
    verify_user_snapshot,
)


@click.group("user")
def user() -> None:
    """Manage Jellyfin users."""


def _resolve_password(password: str | None, no_password: bool) -> str | None:
    if password is not None and no_password:
        message = "--password and --no-password cannot be used together."
        raise click.UsageError(message)
    if password is None and not no_password:
        return click.prompt("Password", hide_input=True, confirmation_prompt=True)
    return password


def _normalise_usernames(source_username: str, username: str) -> tuple[str, str]:
    source_username = source_username.strip()
    username = username.strip()
    if not source_username:
        message = "SOURCE_USERNAME cannot be empty."
        raise click.UsageError(message)
    if not username:
        message = "USERNAME cannot be empty."
        raise click.UsageError(message)
    if source_username.casefold() == username.casefold():
        message = "SOURCE_USERNAME and USERNAME must be different."
        raise click.UsageError(message)
    return source_username, username


def _validate_clone_inputs(
    source_username: str,
    username: str,
    email: str,
    password: str | None,
    no_password: bool,
) -> tuple[str, str, str]:
    source_username, username = _normalise_usernames(source_username, username)
    email = email.strip()
    if not email:
        message = "--email cannot be empty."
        raise click.UsageError(message)
    if password is not None and no_password:
        message = "--password and --no-password cannot be used together."
        raise click.UsageError(message)
    return source_username, username, email


def _find_named_user(users: list[dict], username: str) -> dict | None:
    return next(
        (existing for existing in users if existing.get("Name", "").casefold() == username.casefold()),
        None,
    )


def _require_user_id(user_record: dict, username: str) -> str:
    user_id = user_record.get("Id")
    if not user_id:
        message = f'Jellyfin user "{username}" has no ID.'
        raise click.ClickException(message)
    return str(user_id)


def _find_clone_users(
    users: list[dict],
    source_username: str,
    username: str,
) -> tuple[dict, str, dict | None, str | None]:
    source_user = _find_named_user(users, source_username)
    if source_user is None:
        message = f'Jellyfin user "{source_username}" does not exist.'
        raise click.UsageError(message)
    source_user_id = _require_user_id(source_user, source_username)

    destination_user = _find_named_user(users, username)
    destination_user_id = (
        _require_user_id(destination_user, username) if destination_user is not None else None
    )
    return source_user, source_user_id, destination_user, destination_user_id


def _ensure_jellyseerr_identity_available(
    server: str,
    token: str,
    username: str,
    email: str,
    linked_user: dict | None,
) -> None:
    linked_user_id = str(linked_user.get("id")) if linked_user and linked_user.get("id") is not None else None
    email_conflicts = [
        existing
        for existing in get_jellyseerr_users(server, token, email)
        if str(existing.get("email") or "").casefold() == email.casefold()
        and str(existing.get("id")) != linked_user_id
    ]
    if email_conflicts:
        message = f'A Jellyseerr user with email "{email}" already exists.'
        raise click.UsageError(message)

    username_conflicts = [
        existing
        for existing in get_jellyseerr_users(server, token, username)
        if any(
            str(existing.get(field) or "").casefold() == username.casefold()
            for field in ("username", "jellyfinUsername")
        )
        and str(existing.get("id")) != linked_user_id
    ]
    if username_conflicts:
        message = f'A Jellyseerr user named "{username}" already exists.'
        raise click.UsageError(message)


def _ensure_jellyseerr_destination(
    server: str,
    token: str,
    jellyfin_user_id: str,
    username: str,
    email: str,
    linked_user: dict | None,
) -> tuple[int, dict]:
    jellyseerr_user = linked_user or import_jellyfin_user(server, token, jellyfin_user_id)
    if jellyseerr_user is None:
        jellyseerr_user = find_linked_jellyseerr_user(server, token, jellyfin_user_id, username)
    if jellyseerr_user is None or jellyseerr_user.get("id") is None:
        message = "Jellyseerr did not return the imported destination user. Re-run the command to resume."
        raise click.ClickException(message)
    try:
        jellyseerr_user_id = int(jellyseerr_user["id"])
    except (TypeError, ValueError) as error:
        message = "Jellyseerr returned an invalid destination user ID."
        raise click.ClickException(message) from error

    email_matches = str(jellyseerr_user.get("email") or "").casefold() == email.casefold()
    identity_matches = any(
        str(jellyseerr_user.get(field) or "").casefold() == username.casefold()
        for field in ("username", "jellyfinUsername")
    )
    if email_matches and identity_matches:
        return jellyseerr_user_id, jellyseerr_user

    profile = update_jellyseerr_profile(server, token, jellyseerr_user_id, username, email)
    return jellyseerr_user_id, profile


DETAIL_LIMIT = 25


def _item_difference_payload(item: ItemDifference) -> dict[str, object]:
    return {
        "id": item.item_id,
        "name": item.name,
        "type": item.item_type,
        "fields": list(item.fields),
        "expected": dict(item.expected),
        "actual": dict(item.actual),
    }


def _verification_payload(
    verification: VerificationResult,
    detail_limit: int | None = DETAIL_LIMIT,
) -> dict[str, object]:
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
        "missing_items": [_item_difference_payload(item) for item in missing_items],
        "mismatched_item_count": len(verification.mismatched_items),
        "mismatched_item_ids": [item.item_id for item in mismatched_items],
        "mismatched_items": [_item_difference_payload(item) for item in mismatched_items],
        "missing_playlists": list(verification.missing_playlists),
        "details_truncated": returned_details < total_details,
    }


def _difference_detail(item: ItemDifference) -> str:
    expected = dict(item.expected)
    if not item.fields:
        values = "; ".join(f"{field}: {value!r}" for field, value in item.expected)
        return f"record absent; expected {values}"
    actual = dict(item.actual)
    return "; ".join(f"{field}: {expected[field]!r} → {actual[field]!r}" for field in item.fields)


def _verification_tables(verification: VerificationResult) -> tuple[Table, ...]:
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


def _write_verification_details(
    path: pathlib.Path,
    source_name: str,
    destination_name: str,
    verification: VerificationResult,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source_username": source_name,
        "destination_username": destination_name,
        **_verification_payload(verification, detail_limit=None),
    }
    path.write_bytes(orjson.dumps(payload, option=orjson.OPT_INDENT_2) + b"\n")


def _emit_verification_report(
    source_name: str,
    destination_name: str,
    verification: VerificationResult,
    output_format: OutputFormat,
    details_file: pathlib.Path | None = None,
) -> None:
    if details_file is not None:
        _write_verification_details(details_file, source_name, destination_name, verification)
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
                **_verification_payload(verification),
                "details_file": str(details_file) if details_file is not None else None,
                "message": title,
            },
            summary=(
                ("Verified", verification.verified),
                (
                    "Watch state",
                    f"{verification.watch_items_matched}/{verification.watch_items_expected}",
                ),
                (
                    "Favorites",
                    f"{verification.favorites_matched}/{verification.favorites_expected}",
                ),
                (
                    "Playlists",
                    f"{verification.playlists_matched}/{verification.playlists_expected}",
                ),
                ("Direct item checks", verification.direct_item_checks),
                ("Direct matches", verification.direct_item_matches),
                ("Duplicate source rows removed", verification.duplicate_source_rows_removed),
                ("Missing item records", len(verification.missing_items)),
                ("Mismatched item records", len(verification.mismatched_items)),
            ),
            tables=_verification_tables(verification),
            notes=notes,
        ),
        output_format,
    )


def _emit_clone_report(
    source_name: str,
    destination_name: str,
    email: str,
    jellyfin_user_id: str,
    jellyseerr_user_id: int,
    counts: CloneCounts,
    verification: VerificationResult,
    destination_created: bool,
    password_set: bool | None,
    output_format: OutputFormat,
) -> None:
    action = "Cloned" if destination_created else "Resumed and verified clone of"
    title = f'{action} Jellyfin user "{source_name}" to "{destination_name}".'
    payload = {
        "created": destination_created,
        "resumed": not destination_created,
        "source_username": source_name,
        "username": destination_name,
        "email": email,
        "jellyfin_user_id": jellyfin_user_id,
        "jellyseerr_user_id": jellyseerr_user_id,
        "watch_items_copied": counts.watch_items,
        "favorites_copied": counts.favorites,
        "playlists_copied": counts.playlists,
        "playlist_items_copied": counts.playlist_items,
        "user_data_updated": counts.user_data_updated,
        "user_data_reused": counts.user_data_reused,
        "playlists_created": counts.playlists_created,
        "playlists_reused": counts.playlists_reused,
        "jellyseerr_requests_copied": 0,
        "password_set": password_set,
        **_verification_payload(verification),
        "message": title,
    }
    notes = ["Jellyseerr request ownership was left unchanged."]
    if not destination_created:
        notes.append("The existing destination password was left unchanged.")
    emit(
        Report(
            title=title,
            payload=payload,
            summary=(
                ("Verified", verification.verified),
                ("Destination created", destination_created),
                ("Jellyfin ID", jellyfin_user_id),
                ("Jellyseerr ID", jellyseerr_user_id),
                ("Email", email),
                (
                    "Watch state",
                    f"{verification.watch_items_matched}/{verification.watch_items_expected}",
                ),
                (
                    "Favorites",
                    f"{verification.favorites_matched}/{verification.favorites_expected}",
                ),
                (
                    "Playlists",
                    f"{verification.playlists_matched}/{verification.playlists_expected}",
                ),
                ("User-data records updated", counts.user_data_updated),
                ("User-data records reused", counts.user_data_reused),
                ("Playlists created", counts.playlists_created),
                ("Playlists reused", counts.playlists_reused),
                ("Password set on create", password_set),
            ),
            notes=tuple(notes),
        ),
        output_format,
    )


@user.command("add")
@click.argument("username")
@connection_options
@click.option(
    "--password",
    hide_input=True,
    confirmation_prompt=True,
    help="User password. If omitted, you will be prompted securely.",
)
@click.option("--no-password", is_flag=True, help="Create an account without a password.")
@output_option
def add(
    username: str,
    base_url: str,
    token: str,
    password: str | None,
    no_password: bool,
    output_format: OutputFormat,
) -> None:
    """Create a Jellyfin user."""
    username = username.strip()
    if not username:
        message = "USERNAME cannot be empty."
        raise click.UsageError(message)
    password = _resolve_password(password, no_password)

    headers = build_headers(token)
    existing_users = get_users(base_url, headers)
    if any(existing.get("Name", "").casefold() == username.casefold() for existing in existing_users):
        message = f'A user named "{username}" already exists.'
        raise click.UsageError(message)

    created_user = create_user(base_url, headers, username, password)
    user_id = created_user.get("Id", "unknown")
    created_name = created_user.get("Name", username)
    title = f'Created user "{created_name}" (ID: {user_id}).'
    emit(
        Report(
            title=title,
            payload={
                "created": True,
                "username": created_name,
                "id": user_id,
                "password_set": password is not None,
                "message": title,
            },
            summary=(
                ("Username", created_name),
                ("ID", user_id),
                ("Password set", password is not None),
            ),
        ),
        output_format,
    )


@user.command("clone")
@click.argument("source_username")
@click.argument("username")
@connection_options
@jellyseerr_options(required=True)
@click.option("--email", required=True, help="Email for the linked Jellyseerr account.")
@click.option(
    "--password",
    hide_input=True,
    confirmation_prompt=True,
    help="New Jellyfin password. If omitted, you will be prompted securely.",
)
@click.option("--no-password", is_flag=True, help="Create the new account without a password.")
@click.option(
    "--details-file",
    type=click.Path(path_type=pathlib.Path, dir_okay=False),
    help="Write complete verification differences to a JSON file.",
)
@output_option
def clone_user(
    source_username: str,
    username: str,
    base_url: str,
    token: str,
    jellyseerr_server: str,
    jellyseerr_token: str,
    email: str,
    password: str | None,
    no_password: bool,
    details_file: pathlib.Path | None,
    output_format: OutputFormat,
) -> None:
    """Create or resume USERNAME with a copy of SOURCE_USERNAME's data."""
    source_username, username, email = _validate_clone_inputs(
        source_username,
        username,
        email,
        password,
        no_password,
    )
    headers = build_headers(token)
    source_user, source_user_id, destination_user, destination_user_id = _find_clone_users(
        get_users(base_url, headers),
        source_username,
        username,
    )

    check_jellyseerr_user_management_access(jellyseerr_server, jellyseerr_token)
    linked_user = (
        find_linked_jellyseerr_user(
            jellyseerr_server,
            jellyseerr_token,
            destination_user_id,
            username,
        )
        if destination_user_id is not None
        else None
    )
    _ensure_jellyseerr_identity_available(
        jellyseerr_server,
        jellyseerr_token,
        username,
        email,
        linked_user,
    )

    source_snapshot = capture_user_snapshot(base_url, headers, source_user_id)
    destination_created = destination_user_id is None
    password_set: bool | None = None
    existing_snapshot = None
    if destination_user_id is None:
        password = _resolve_password(password, no_password)
        destination_user = create_user(base_url, headers, username, password)
        destination_user_id = _require_user_id(destination_user, username)
        password_set = password is not None
    else:
        existing_snapshot = resolve_destination_snapshot(
            base_url,
            headers,
            destination_user_id,
            source_snapshot,
            capture_user_snapshot(base_url, headers, destination_user_id),
        )

    counts = apply_user_snapshot(
        base_url,
        headers,
        destination_user_id,
        source_snapshot,
        existing_snapshot,
    )
    destination_snapshot = resolve_destination_snapshot(
        base_url,
        headers,
        destination_user_id,
        source_snapshot,
        capture_user_snapshot(base_url, headers, destination_user_id),
    )
    verification = verify_user_snapshot(source_snapshot, destination_snapshot)
    if not verification.verified:
        _emit_verification_report(
            str(source_user.get("Name") or source_username),
            str((destination_user or {}).get("Name") or username),
            verification,
            output_format,
            details_file,
        )
        raise click.exceptions.Exit(1)

    jellyseerr_user_id, profile = _ensure_jellyseerr_destination(
        jellyseerr_server,
        jellyseerr_token,
        destination_user_id,
        username,
        email,
        linked_user,
    )
    _emit_clone_report(
        str(source_user.get("Name") or source_username),
        str((destination_user or {}).get("Name") or username),
        str(profile.get("email") or email),
        destination_user_id,
        jellyseerr_user_id,
        counts,
        verification,
        destination_created,
        password_set,
        output_format,
    )


@user.command("verify-clone")
@click.argument("source_username")
@click.argument("username")
@connection_options
@click.option(
    "--details-file",
    type=click.Path(path_type=pathlib.Path, dir_okay=False),
    help="Write complete verification differences to a JSON file.",
)
@output_option
def verify_clone(
    source_username: str,
    username: str,
    base_url: str,
    token: str,
    details_file: pathlib.Path | None,
    output_format: OutputFormat,
) -> None:
    """Compare USERNAME's watch state, favorites, and playlists with SOURCE_USERNAME."""
    source_username, username = _normalise_usernames(source_username, username)
    users = get_users(base_url, build_headers(token))
    source_user = _find_named_user(users, source_username)
    destination_user = _find_named_user(users, username)
    if source_user is None:
        message = f'Jellyfin user "{source_username}" does not exist.'
        raise click.UsageError(message)
    if destination_user is None:
        message = f'Jellyfin user "{username}" does not exist.'
        raise click.UsageError(message)

    headers = build_headers(token)
    source_snapshot = capture_user_snapshot(
        base_url,
        headers,
        _require_user_id(source_user, source_username),
    )
    destination_user_id = _require_user_id(destination_user, username)
    destination_snapshot = resolve_destination_snapshot(
        base_url,
        headers,
        destination_user_id,
        source_snapshot,
        capture_user_snapshot(base_url, headers, destination_user_id),
    )
    verification = verify_user_snapshot(source_snapshot, destination_snapshot)
    _emit_verification_report(
        str(source_user.get("Name") or source_username),
        str(destination_user.get("Name") or username),
        verification,
        output_format,
        details_file,
    )
