"""User-management commands for Jellyfin."""

from __future__ import annotations

import click

from jellyfin_utils.client import build_headers, create_user, get_users


@click.group("user")
def user() -> None:
    """Manage Jellyfin users."""


@user.command("add")
@click.argument("username")
@click.option(
    "--server",
    envvar="JELLYFIN_SERVER",
    required=True,
    help="Jellyfin server base URL (e.g. http://jellyfin.lan:8096).",
)
@click.option(
    "--token",
    envvar="JELLYFIN_TOKEN",
    required=True,
    help="Jellyfin API key with user-management permission.",
)
@click.option(
    "--password",
    hide_input=True,
    confirmation_prompt=True,
    help="User password. If omitted, you will be prompted securely.",
)
@click.option(
    "--no-password",
    is_flag=True,
    help="Create an account without a password instead of prompting.",
)
def add(
    username: str,
    server: str,
    token: str,
    password: str | None,
    no_password: bool,  # noqa: FBT001
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

    base_url = server.rstrip("/")
    headers = build_headers(token)
    existing_users = get_users(base_url, headers)
    if any(existing.get("Name", "").casefold() == username.casefold() for existing in existing_users):
        message = f'A user named "{username}" already exists.'
        raise click.UsageError(message)

    created_user = create_user(base_url, headers, username, password)
    user_id = created_user.get("Id", "unknown")
    click.echo(f'Created user "{created_user.get("Name", username)}" (ID: {user_id}).')
