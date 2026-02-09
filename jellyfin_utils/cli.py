"""Unified CLI entry point for all jellyfin-utils commands."""

import click

from jellyfin_utils.stale.cli import main as stale
from jellyfin_utils.watched.cli import main as watched


@click.group("jellyfin")
@click.version_option(package_name="jellyfin-utils")
def cli() -> None:
    """Manage a Jellyfin media server."""


cli.add_command(watched, "watched")
cli.add_command(stale, "stale")
