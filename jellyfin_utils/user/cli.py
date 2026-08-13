"""User-management commands for Jellyfin."""

from __future__ import annotations

import click

from jellyfin_utils.client import build_headers, create_user, get_users
from jellyfin_utils.options import connection_options, output_option
from jellyfin_utils.output import OutputFormat, Report, emit


@click.group("user")
def user() -> None:
    """Manage Jellyfin users."""


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
    if password is not None and no_password:
        message = "--password and --no-password cannot be used together."
        raise click.UsageError(message)
    if password is None and not no_password:
        password = click.prompt("Password", hide_input=True, confirmation_prompt=True)

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
