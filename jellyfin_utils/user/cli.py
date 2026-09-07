"""User-management commands for Jellyfin."""

from __future__ import annotations

import pathlib

import click

from jellyfin_utils.client import build_headers, create_user, get_users
from jellyfin_utils.options import connection_options, jellyseerr_options, output_option
from jellyfin_utils.output import OutputFormat, Report, emit
from jellyfin_utils.user.models import CloneUserOutcome, CloneUserRequest
from jellyfin_utils.user.render import emit_clone_report, emit_verification_report
from jellyfin_utils.user.workflow import clone_user as run_clone_user, verify_clone as run_verify_clone


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


def _workflow_error(error: ValueError | RuntimeError) -> click.ClickException:
    if isinstance(error, ValueError):
        return click.UsageError(str(error))
    return click.ClickException(str(error))


def _execute_clone(request: CloneUserRequest) -> CloneUserOutcome:
    try:
        return run_clone_user(request)
    except (ValueError, RuntimeError) as error:
        raise _workflow_error(error) from error


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
    outcome = _execute_clone(
        CloneUserRequest(
            source_username=source_username,
            username=username,
            base_url=base_url,
            token=token,
            jellyseerr_server=jellyseerr_server,
            jellyseerr_token=jellyseerr_token,
            email=email,
            password=password,
            no_password=no_password,
            password_resolver=_resolve_password,
        )
    )
    if not outcome.verification.verified:
        emit_verification_report(
            outcome.source_name,
            outcome.destination_name,
            outcome.verification,
            output_format,
            details_file,
        )
        raise click.exceptions.Exit(1)
    emit_clone_report(outcome, output_format)


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
    try:
        source_name, destination_name, verification = run_verify_clone(
            base_url,
            token,
            source_username,
            username,
        )
    except (ValueError, RuntimeError) as error:
        raise _workflow_error(error) from error
    emit_verification_report(
        source_name,
        destination_name,
        verification,
        output_format,
        details_file,
    )
