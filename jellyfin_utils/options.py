"""
Shared Click options for every jellyfin-utils command.

Any option more than one command needs lives here, so its flag name, type,
default, and help text are defined once. Commands stack the decorators in a
fixed order:

.. code-block:: python

    @click.command("name")
    @connection_options
    @jellyseerr_options
    # ...command-specific options...
    @output_option
    @quiet_option

The connection decorators normalise their URL in a Click callback and pass it as
``base_url``, so command bodies receive a URL that is already free of a trailing
slash and never have to call ``rstrip("/")``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import click

from jellyfin_utils.output import OutputFormat

if TYPE_CHECKING:
    from collections.abc import Callable

    # A Click option decorator: takes a command (or another decorated command)
    # and returns it with one more parameter attached.
    Decorator = Callable[[Callable[..., Any]], Callable[..., Any]]

__all__ = [
    "connection_options",
    "ignore_user_option",
    "jellyseerr_options",
    "output_option",
    "quiet_option",
    "require_jellyseerr_pair",
    "threshold_option",
]


def _normalise_url(
    _ctx: click.Context,
    _param: click.Parameter,
    value: str | None,
) -> str | None:
    """Strip trailing slashes so ``f"{base_url}{path}"`` never doubles them up."""
    return value.rstrip("/") if value else value


def connection_options(command: Callable[..., Any]) -> Callable[..., Any]:
    """Add the required ``--server`` and ``--token`` options for Jellyfin."""
    command = click.option("--token", envvar="JELLYFIN_TOKEN", required=True, help="Jellyfin API key.")(
        command
    )
    return click.option(
        "--server",
        "base_url",
        envvar="JELLYFIN_SERVER",
        required=True,
        callback=_normalise_url,
        help="Jellyfin server base URL (e.g. http://jellyfin.lan:8096).",
    )(command)


def jellyseerr_options(
    command: Callable[..., Any] | None = None,
    *,
    required: bool = False,
) -> Callable[..., Any] | Decorator:
    """
    Add the ``--jellyseerr-server`` and ``--jellyseerr-token`` options.

    Usable bare (``@jellyseerr_options``) for the optional pair that enables
    requester prioritisation, or as ``@jellyseerr_options(required=True)`` for
    commands that cannot run without Jellyseerr.
    """

    def decorate(command: Callable[..., Any]) -> Callable[..., Any]:
        command = click.option(
            "--jellyseerr-token",
            envvar="JELLYSEERR_TOKEN",
            required=required,
            help="Jellyseerr API key."
            if required
            else "Jellyseerr API key; required with --jellyseerr-server.",
        )(command)
        return click.option(
            "--jellyseerr-server",
            envvar="JELLYSEERR_SERVER",
            required=required,
            callback=_normalise_url,
            help="Jellyseerr server base URL."
            if required
            else "Jellyseerr server base URL; enables requester-watch prioritization.",
        )(command)

    return decorate if command is None else decorate(command)


def ignore_user_option(command: Callable[..., Any]) -> Callable[..., Any]:
    """Add the repeatable ``--ignore-user`` option."""
    return click.option("--ignore-user", multiple=True, help="Username to ignore (repeatable).")(command)


def threshold_option(command: Callable[..., Any]) -> Callable[..., Any]:
    """Add the ``--threshold`` watched-percentage option."""
    return click.option(
        "--threshold",
        type=int,
        default=80,
        show_default=True,
        help="Percentage of users who must have watched an item for it to count.",
    )(command)


def output_option(command: Callable[..., Any]) -> Callable[..., Any]:
    """Add the shared ``--output`` format option, defaulting to text."""
    return click.option(
        "--output",
        "output_format",
        type=click.Choice(OutputFormat, case_sensitive=False),
        default=OutputFormat.TEXT.value,
        show_default=True,
        help="Output format.",
    )(command)


def quiet_option(command: Callable[..., Any]) -> Callable[..., Any]:
    """Add the ``--quiet`` option that trims text output to the item list."""
    return click.option("--quiet", is_flag=True, help="In text mode, print only the item list, no summary.")(
        command
    )


def require_jellyseerr_pair(server: str | None, token: str | None) -> None:
    """Reject a half-configured Jellyseerr connection."""
    if bool(server) != bool(token):
        message = "--jellyseerr-server and --jellyseerr-token must be used together."
        raise click.UsageError(message)
