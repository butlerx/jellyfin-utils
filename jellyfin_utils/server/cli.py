"""Safe operational commands for a Jellyfin server."""

from __future__ import annotations

from typing import Any, cast

import click
import orjson

from jellyfin_utils.client import build_headers, get_json, post_empty


def _json(value: object) -> None:
    click.echo(orjson.dumps(value, option=orjson.OPT_INDENT_2).decode())


def _connection_options(command: Any) -> Any:  # noqa: ANN401
    command = click.option("--token", envvar="JELLYFIN_TOKEN", required=True, help="Jellyfin API key.")(
        command
    )
    return click.option("--server", envvar="JELLYFIN_SERVER", required=True, help="Jellyfin server URL.")(
        command
    )


def _tasks(base_url: str, headers: dict[str, str]) -> list[dict]:
    return cast("list[dict]", get_json(base_url, headers, "/ScheduledTasks"))


def _matching_task(tasks: list[dict], task_query: str) -> dict:
    query = task_query.casefold()
    matches = [
        task
        for task in tasks
        if query
        in {
            str(task.get("Id", "")).casefold(),
            str(task.get("Name", "")).casefold(),
            str(task.get("Key", "")).casefold(),
        }
    ]
    if len(matches) != 1:
        message = (
            f'Expected one task matching "{task_query}", found {len(matches)}. Run --list to see task IDs.'
        )
        raise click.UsageError(message)
    return matches[0]


@click.group()
def server() -> None:
    """Inspect and operate a Jellyfin server."""


@server.command()
@_connection_options
def status(server: str, token: str) -> None:
    """Show server, storage, task, and active-session status."""
    base_url = server.rstrip("/")
    headers = build_headers(token)
    info = cast("dict", get_json(base_url, headers, "/System/Info"))
    storage = cast("dict", get_json(base_url, headers, "/System/Info/Storage"))
    tasks = _tasks(base_url, headers)
    sessions = cast("list[dict]", get_json(base_url, headers, "/Sessions"))
    failed = [
        task.get("Name")
        for task in tasks
        if (task.get("LastExecutionResult") or {}).get("Status") == "Failed"
    ]
    _json(
        {
            "server": info.get("ServerName"),
            "version": info.get("Version"),
            "pending_restart": info.get("HasPendingRestart"),
            "libraries": len(storage.get("Libraries") or []),
            "active_sessions": sum(bool(session.get("IsActive")) for session in sessions),
            "failed_tasks": failed,
        }
    )


@server.command()
@_connection_options
@click.option("--library", help="Optional library ID to refresh; omit for all libraries.")
@click.option("--apply", is_flag=True, help="Actually start the scan.")
def scan(server: str, token: str, library: str | None, apply: bool) -> None:  # noqa: FBT001
    """Preview or start a library scan."""
    if not apply:
        click.echo("Dry run: no scan started. Re-run with --apply to start it.")
        return
    base_url = server.rstrip("/")
    path = f"/Items/{library}/Refresh" if library else "/Library/Refresh"
    post_empty(base_url, build_headers(token), path)
    click.echo("Library scan started.")


@server.command()
@_connection_options
@click.option("--list", "list_tasks", is_flag=True, help="List runnable scheduled tasks.")
@click.option("--task", help="Exact scheduled-task ID, name, or key to start.")
@click.option("--apply", is_flag=True, help="Actually start the selected task.")
def maintenance(server: str, token: str, list_tasks: bool, task: str | None, apply: bool) -> None:  # noqa: FBT001
    """List or run Jellyfin's built-in scheduled maintenance tasks."""
    base_url = server.rstrip("/")
    tasks = _tasks(base_url, build_headers(token))
    if list_tasks or task is None:
        _json(
            {
                "tasks": [
                    {
                        "id": item.get("Id"),
                        "name": item.get("Name"),
                        "key": item.get("Key"),
                        "state": item.get("State"),
                        "last_result": item.get("LastExecutionResult"),
                    }
                    for item in tasks
                ]
            }
        )
        return
    selected = _matching_task(tasks, task)
    if not apply:
        click.echo(f'Dry run: would start "{selected.get("Name")}". Re-run with --apply.')
        return
    post_empty(base_url, build_headers(token), f"/ScheduledTasks/Running/{selected['Id']}")
    click.echo(f'Started "{selected.get("Name")}".')


@server.command()
@_connection_options
@click.option("--stop", "session_id", help="Session ID to stop.")
@click.option("--confirm", is_flag=True, help="Required with --stop.")
def sessions(server: str, token: str, session_id: str | None, confirm: bool) -> None:  # noqa: FBT001
    """List sessions or stop a selected active playback session."""
    base_url = server.rstrip("/")
    headers = build_headers(token)
    if session_id is not None:
        if not confirm:
            message = "Stopping a session requires --confirm."
            raise click.UsageError(message)
        post_empty(base_url, headers, f"/Sessions/{session_id}/Playing/Stop")
        click.echo(f"Stop command sent to session {session_id}.")
        return
    active = cast("list[dict]", get_json(base_url, headers, "/Sessions", params={"activeWithinSeconds": 300}))
    _json(
        {
            "sessions": [
                {
                    "id": item.get("Id"),
                    "user": item.get("UserName"),
                    "client": item.get("Client"),
                    "device": item.get("DeviceName"),
                    "playing": (item.get("NowPlayingItem") or {}).get("Name"),
                    "transcoding": bool(item.get("TranscodingInfo")),
                }
                for item in active
            ]
        }
    )


@server.command()
@_connection_options
@click.option("--apply", is_flag=True, help="Actually start selected maintenance tasks.")
@click.option("--confirm", is_flag=True, help="Required with --apply.")
def cleanup(server: str, token: str, apply: bool, confirm: bool) -> None:  # noqa: FBT001
    """Preview or run installed cleanup-oriented scheduled tasks."""
    base_url = server.rstrip("/")
    headers = build_headers(token)
    keywords = ("clean", "cache", "image", "metadata", "optim")
    candidates = [
        task
        for task in _tasks(base_url, headers)
        if any(
            word in f"{task.get('Name', '')} {task.get('Description', '')}".casefold() for word in keywords
        )
    ]
    if not apply:
        _json(
            {
                "dry_run": True,
                "tasks": [{"id": task.get("Id"), "name": task.get("Name")} for task in candidates],
            }
        )
        return
    if not confirm:
        message = "Running cleanup tasks requires --confirm."
        raise click.UsageError(message)
    for task in candidates:
        post_empty(base_url, headers, f"/ScheduledTasks/Running/{task['Id']}")
    click.echo(f"Started {len(candidates)} cleanup task(s).")
