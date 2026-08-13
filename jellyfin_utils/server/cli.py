"""Safe operational commands for a Jellyfin server."""

from __future__ import annotations

from typing import cast

import click

from jellyfin_utils.client import build_headers, get_json, post_empty
from jellyfin_utils.options import connection_options, output_option
from jellyfin_utils.output import OutputFormat, Report, Table, emit


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


def _action_report(title: str, payload: dict, summary: tuple[tuple[str, object], ...]) -> Report:
    """Build a report for a command that acts on the server rather than listing data."""
    return Report(title=title, payload={**payload, "message": title}, summary=summary)


@server.command()
@connection_options
@output_option
def status(base_url: str, token: str, output_format: OutputFormat) -> None:
    """Show server, storage, task, and active-session status."""
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
    payload = {
        "server": info.get("ServerName"),
        "version": info.get("Version"),
        "pending_restart": info.get("HasPendingRestart"),
        "libraries": len(storage.get("Libraries") or []),
        "active_sessions": sum(bool(session.get("IsActive")) for session in sessions),
        "failed_tasks": failed,
    }
    emit(
        Report(
            title="Server status",
            payload=payload,
            summary=(
                ("Server", payload["server"]),
                ("Version", payload["version"]),
                ("Pending restart", payload["pending_restart"]),
                ("Libraries", payload["libraries"]),
                ("Active sessions", payload["active_sessions"]),
            ),
            tables=(
                Table(
                    title="Scheduled tasks whose last run failed",
                    columns=("Task",),
                    rows=[(name,) for name in failed],
                    empty="No failed tasks.",
                ),
            ),
        ),
        output_format,
    )


@server.command()
@connection_options
@click.option("--library", help="Optional library ID to refresh; omit for all libraries.")
@click.option("--apply", is_flag=True, help="Actually start the scan.")
@output_option
def scan(base_url: str, token: str, library: str | None, apply: bool, output_format: OutputFormat) -> None:
    """Preview or start a library scan."""
    target = library or "all libraries"
    if not apply:
        emit(
            _action_report(
                "Dry run: no scan started. Re-run with --apply to start it.",
                {"action": "scan", "library": library, "applied": False, "dry_run": True},
                (("Action", "scan"), ("Target", target), ("Applied", False)),
            ),
            output_format,
        )
        return
    path = f"/Items/{library}/Refresh" if library else "/Library/Refresh"
    post_empty(base_url, build_headers(token), path)
    emit(
        _action_report(
            "Library scan started.",
            {"action": "scan", "library": library, "applied": True, "dry_run": False},
            (("Action", "scan"), ("Target", target), ("Applied", True)),
        ),
        output_format,
    )


@server.command()
@connection_options
@click.option("--list", "list_tasks", is_flag=True, help="List runnable scheduled tasks.")
@click.option("--task", help="Exact scheduled-task ID, name, or key to start.")
@click.option("--apply", is_flag=True, help="Actually start the selected task.")
@output_option
def maintenance(
    base_url: str,
    token: str,
    list_tasks: bool,
    task: str | None,
    apply: bool,
    output_format: OutputFormat,
) -> None:
    """List or run Jellyfin's built-in scheduled maintenance tasks."""
    tasks = _tasks(base_url, build_headers(token))
    if list_tasks or task is None:
        listed = [
            {
                "id": item.get("Id"),
                "name": item.get("Name"),
                "key": item.get("Key"),
                "state": item.get("State"),
                "last_result": item.get("LastExecutionResult"),
            }
            for item in tasks
        ]
        emit(
            Report(
                title="Scheduled tasks",
                payload={"tasks": listed},
                summary=(("Tasks", len(listed)),),
                tables=(
                    Table(
                        columns=("Name", "State", "Last result", "Key", "ID"),
                        rows=[
                            (
                                item["name"],
                                item["state"],
                                (item["last_result"] or {}).get("Status"),
                                item["key"],
                                item["id"],
                            )
                            for item in listed
                        ],
                        empty="No scheduled tasks.",
                    ),
                ),
            ),
            output_format,
        )
        return
    selected = _matching_task(tasks, task)
    name = selected.get("Name")
    if not apply:
        emit(
            _action_report(
                f'Dry run: would start "{name}". Re-run with --apply.',
                {"action": "start_task", "task": name, "id": selected["Id"], "applied": False},
                (("Action", "start_task"), ("Task", name), ("Applied", False)),
            ),
            output_format,
        )
        return
    post_empty(base_url, build_headers(token), f"/ScheduledTasks/Running/{selected['Id']}")
    emit(
        _action_report(
            f'Started "{name}".',
            {"action": "start_task", "task": name, "id": selected["Id"], "applied": True},
            (("Action", "start_task"), ("Task", name), ("Applied", True)),
        ),
        output_format,
    )


@server.command()
@connection_options
@click.option("--stop", "session_id", help="Session ID to stop.")
@click.option("--confirm", is_flag=True, help="Required with --stop.")
@output_option
def sessions(
    base_url: str,
    token: str,
    session_id: str | None,
    confirm: bool,
    output_format: OutputFormat,
) -> None:
    """List sessions or stop a selected active playback session."""
    headers = build_headers(token)
    if session_id is not None:
        if not confirm:
            message = "Stopping a session requires --confirm."
            raise click.UsageError(message)
        post_empty(base_url, headers, f"/Sessions/{session_id}/Playing/Stop")
        emit(
            _action_report(
                f"Stop command sent to session {session_id}.",
                {"action": "stop_session", "session": session_id, "applied": True},
                (("Action", "stop_session"), ("Session", session_id), ("Applied", True)),
            ),
            output_format,
        )
        return
    active = cast("list[dict]", get_json(base_url, headers, "/Sessions", params={"activeWithinSeconds": 300}))
    listed = [
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
    emit(
        Report(
            title="Active sessions",
            payload={"sessions": listed},
            summary=(
                ("Sessions", len(listed)),
                ("Transcoding", sum(bool(item["transcoding"]) for item in listed)),
            ),
            tables=(
                Table(
                    columns=("User", "Client", "Device", "Playing", "Transcoding", "ID"),
                    rows=[
                        (
                            item["user"],
                            item["client"],
                            item["device"],
                            item["playing"],
                            item["transcoding"],
                            item["id"],
                        )
                        for item in listed
                    ],
                    empty="No sessions active in the last 5 minutes.",
                ),
            ),
        ),
        output_format,
    )


@server.command()
@connection_options
@click.option("--apply", is_flag=True, help="Actually start selected maintenance tasks.")
@click.option("--confirm", is_flag=True, help="Required with --apply.")
@output_option
def cleanup(base_url: str, token: str, apply: bool, confirm: bool, output_format: OutputFormat) -> None:
    """Preview or run installed cleanup-oriented scheduled tasks."""
    headers = build_headers(token)
    keywords = ("clean", "cache", "image", "metadata", "optim")
    candidates = [
        task
        for task in _tasks(base_url, headers)
        if any(
            word in f"{task.get('Name', '')} {task.get('Description', '')}".casefold() for word in keywords
        )
    ]
    listed = [{"id": task.get("Id"), "name": task.get("Name")} for task in candidates]
    if not apply:
        emit(
            Report(
                title="Cleanup tasks (dry run)",
                payload={"dry_run": True, "tasks": listed},
                summary=(("Matching tasks", len(listed)), ("Applied", False)),
                tables=(
                    Table(
                        columns=("Name", "ID"),
                        rows=[(task["name"], task["id"]) for task in listed],
                        empty="No cleanup-oriented tasks installed.",
                    ),
                ),
                notes=("Re-run with --apply --confirm to start these tasks.",),
            ),
            output_format,
        )
        return
    if not confirm:
        message = "Running cleanup tasks requires --confirm."
        raise click.UsageError(message)
    for task in candidates:
        post_empty(base_url, headers, f"/ScheduledTasks/Running/{task['Id']}")
    emit(
        Report(
            title=f"Started {len(candidates)} cleanup task(s).",
            payload={"dry_run": False, "tasks": listed, "started": len(listed), "message": "Started tasks."},
            summary=(("Started", len(listed)), ("Applied", True)),
            tables=(
                Table(
                    columns=("Name", "ID"),
                    rows=[(task["name"], task["id"]) for task in listed],
                    empty="No cleanup-oriented tasks installed.",
                ),
            ),
        ),
        output_format,
    )
