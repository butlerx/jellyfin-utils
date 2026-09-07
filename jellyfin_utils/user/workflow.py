"""Orchestration for cloning and verifying Jellyfin users."""

from __future__ import annotations

from dataclasses import replace

from jellyfin_utils.client import build_headers, create_user, get_users
from jellyfin_utils.jellyseerr import (
    check_jellyseerr_user_management_access,
    find_linked_jellyseerr_user,
    get_jellyseerr_users,
    import_jellyfin_user,
    update_jellyseerr_profile,
)
from jellyfin_utils.user.models import (
    CloneCounts,
    CloneUserOutcome,
    CloneUserRequest,
    UserCloneSnapshot,
    VerificationResult,
)
from jellyfin_utils.user.repository import (
    apply_user_snapshot,
    capture_user_snapshot,
    resolve_destination_snapshot,
)
from jellyfin_utils.user.snapshot import verify_user_snapshot


def find_named_user(users: list[dict], username: str) -> dict | None:
    """Find a Jellyfin user by case-insensitive display name."""
    return next(
        (existing for existing in users if existing.get("Name", "").casefold() == username.casefold()),
        None,
    )


def require_user_id(user_record: dict, username: str) -> str:
    """Return a Jellyfin user ID or report an invalid user record."""
    user_id = user_record.get("Id")
    if not user_id:
        message = f'Jellyfin user "{username}" has no ID.'
        raise RuntimeError(message)
    return str(user_id)


def _find_clone_users(
    users: list[dict],
    source_username: str,
    username: str,
) -> tuple[dict, str, dict | None, str | None]:
    source_user = find_named_user(users, source_username)
    if source_user is None:
        message = f'Jellyfin user "{source_username}" does not exist.'
        raise ValueError(message)
    source_user_id = require_user_id(source_user, source_username)

    destination_user = find_named_user(users, username)
    destination_user_id = (
        require_user_id(destination_user, username) if destination_user is not None else None
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
        raise ValueError(message)

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
        raise ValueError(message)


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
        raise RuntimeError(message)
    try:
        jellyseerr_user_id = int(jellyseerr_user["id"])
    except (TypeError, ValueError) as error:
        message = "Jellyseerr returned an invalid destination user ID."
        raise RuntimeError(message) from error

    email_matches = str(jellyseerr_user.get("email") or "").casefold() == email.casefold()
    identity_matches = any(
        str(jellyseerr_user.get(field) or "").casefold() == username.casefold()
        for field in ("username", "jellyfinUsername")
    )
    if email_matches and identity_matches:
        return jellyseerr_user_id, jellyseerr_user

    profile = update_jellyseerr_profile(server, token, jellyseerr_user_id, username, email)
    return jellyseerr_user_id, profile


def _destination_snapshot(
    request: CloneUserRequest,
    headers: dict[str, str],
    destination_user_id: str,
    source_snapshot: UserCloneSnapshot,
) -> UserCloneSnapshot:
    return resolve_destination_snapshot(
        request.base_url,
        headers,
        destination_user_id,
        source_snapshot,
        capture_user_snapshot(request.base_url, headers, destination_user_id),
    )


def _create_destination(
    request: CloneUserRequest,
    headers: dict[str, str],
) -> tuple[dict, str, bool]:
    password = request.password_resolver(request.password, request.no_password)
    destination = create_user(request.base_url, headers, request.username, password)
    return destination, require_user_id(destination, request.username), password is not None


def _prepare_clone(
    request: CloneUserRequest,
    headers: dict[str, str],
) -> tuple[dict, dict | None, str | None, dict | None, UserCloneSnapshot]:
    source, source_id, destination, destination_id = _find_clone_users(
        get_users(request.base_url, headers),
        request.source_username,
        request.username,
    )
    check_jellyseerr_user_management_access(request.jellyseerr_server, request.jellyseerr_token)
    linked_user = (
        find_linked_jellyseerr_user(
            request.jellyseerr_server,
            request.jellyseerr_token,
            destination_id,
            request.username,
        )
        if destination_id is not None
        else None
    )
    _ensure_jellyseerr_identity_available(
        request.jellyseerr_server,
        request.jellyseerr_token,
        request.username,
        request.email,
        linked_user,
    )
    snapshot = capture_user_snapshot(request.base_url, headers, source_id)
    return source, destination, destination_id, linked_user, snapshot


def _copy_jellyfin_state(
    request: CloneUserRequest,
    headers: dict[str, str],
    destination_id: str,
    source_snapshot: UserCloneSnapshot,
    existing_snapshot: UserCloneSnapshot | None,
) -> tuple[CloneCounts, VerificationResult]:
    counts = apply_user_snapshot(
        request.base_url,
        headers,
        destination_id,
        source_snapshot,
        existing_snapshot,
    )
    destination_snapshot = _destination_snapshot(request, headers, destination_id, source_snapshot)
    return counts, verify_user_snapshot(source_snapshot, destination_snapshot)


def clone_user(request: CloneUserRequest) -> CloneUserOutcome:
    """Clone a user across Jellyfin and Jellyseerr without rendering output."""
    headers = build_headers(request.token)
    source, destination, destination_id, linked_user, source_snapshot = _prepare_clone(request, headers)
    destination_created = destination_id is None
    password_set: bool | None = None
    existing_snapshot = None
    if destination_id is None:
        destination, destination_id, password_set = _create_destination(request, headers)
    else:
        existing_snapshot = _destination_snapshot(request, headers, destination_id, source_snapshot)

    counts, verification = _copy_jellyfin_state(
        request,
        headers,
        destination_id,
        source_snapshot,
        existing_snapshot,
    )
    outcome = CloneUserOutcome(
        source_name=str(source.get("Name") or request.source_username),
        destination_name=str((destination or {}).get("Name") or request.username),
        email=request.email,
        jellyfin_user_id=destination_id,
        counts=counts,
        verification=verification,
        destination_created=destination_created,
        password_set=password_set,
    )
    if not verification.verified:
        return outcome

    jellyseerr_user_id, profile = _ensure_jellyseerr_destination(
        request.jellyseerr_server,
        request.jellyseerr_token,
        destination_id,
        request.username,
        request.email,
        linked_user,
    )
    return replace(
        outcome,
        email=str(profile.get("email") or request.email),
        jellyseerr_user_id=jellyseerr_user_id,
    )


def verify_clone(
    base_url: str,
    token: str,
    source_username: str,
    username: str,
) -> tuple[str, str, VerificationResult]:
    """Capture and compare two Jellyfin users without rendering output."""
    headers = build_headers(token)
    users = get_users(base_url, headers)
    source = find_named_user(users, source_username)
    destination = find_named_user(users, username)
    if source is None:
        message = f'Jellyfin user "{source_username}" does not exist.'
        raise ValueError(message)
    if destination is None:
        message = f'Jellyfin user "{username}" does not exist.'
        raise ValueError(message)

    source_snapshot = capture_user_snapshot(
        base_url,
        headers,
        require_user_id(source, source_username),
    )
    destination_id = require_user_id(destination, username)
    destination_snapshot = resolve_destination_snapshot(
        base_url,
        headers,
        destination_id,
        source_snapshot,
        capture_user_snapshot(base_url, headers, destination_id),
    )
    return (
        str(source.get("Name") or source_username),
        str(destination.get("Name") or username),
        verify_user_snapshot(source_snapshot, destination_snapshot),
    )
