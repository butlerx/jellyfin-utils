"""Unified CLI entry point for all jellyfin-utils commands."""

import click

from jellyfin_utils.analysis import duplicates, health, reclaim, report, requests
from jellyfin_utils.server import server
from jellyfin_utils.stale.cli import main as stale
from jellyfin_utils.user.cli import user
from jellyfin_utils.watched.cli import main as watched


@click.group("jellyfin")
@click.version_option(package_name="jellyfin-utils")
def cli() -> None:
    """Manage a Jellyfin media server."""


cli.add_command(watched, "watched")
cli.add_command(stale, "stale")
cli.add_command(user)
cli.add_command(reclaim)
cli.add_command(duplicates)
cli.add_command(health)
cli.add_command(requests)
cli.add_command(report)
cli.add_command(server)
