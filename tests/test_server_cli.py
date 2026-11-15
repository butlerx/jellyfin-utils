"""``server`` commands: dry runs by default, confirmation gates, and the exact calls they make."""

from __future__ import annotations

import orjson
import pytest
import responses
from click.testing import CliRunner, Result
from conftest import BASE_URL

from jellyfin_utils.cli import cli

RUNNER = CliRunner()
CONNECTION = ["--server", BASE_URL, "--token", "t", "--output", "json"]

TASKS = [
    {"Id": "t-clean", "Name": "Clean Cache", "Key": "CleanCache", "State": "Idle", "Description": "cache"},
    {"Id": "t-scan", "Name": "Scan Media", "Key": "ScanLibrary", "State": "Idle", "Description": ""},
    {
        "Id": "t-meta",
        "Name": "Refresh Metadata",
        "Key": "Metadata",
        "State": "Running",
        "Description": "download metadata",
        "LastExecutionResult": {"Status": "Failed"},
    },
]


def _invoke(*args: str) -> Result:
    return RUNNER.invoke(cli, ["server", *args, *CONNECTION])


def _posts() -> list[str]:
    return [str(call.request.url) for call in responses.calls if call.request.method == "POST"]


@responses.activate
def test_status_reports_failed_tasks_and_active_sessions() -> None:
    responses.get(
        f"{BASE_URL}/System/Info", json={"ServerName": "home", "Version": "10.9", "HasPendingRestart": False}
    )
    responses.get(f"{BASE_URL}/System/Info/Storage", json={"Libraries": [{}, {}]})
    responses.get(f"{BASE_URL}/ScheduledTasks", json=TASKS)
    responses.get(f"{BASE_URL}/Sessions", json=[{"IsActive": True}, {"IsActive": False}])

    result = _invoke("status")

    assert result.exit_code == 0, result.output
    payload = orjson.loads(result.output)
    assert payload["server"] == "home"
    assert payload["libraries"] == 2
    assert payload["active_sessions"] == 1
    assert payload["failed_tasks"] == ["Refresh Metadata"]


@responses.activate
def test_scan_without_apply_is_a_dry_run() -> None:
    result = _invoke("scan")

    assert result.exit_code == 0
    payload = orjson.loads(result.output)
    assert payload["applied"] is False
    assert payload["dry_run"] is True
    assert _posts() == []


@responses.activate
def test_scan_apply_refreshes_every_library() -> None:
    responses.post(f"{BASE_URL}/Library/Refresh", status=204)

    result = _invoke("scan", "--apply")

    assert result.exit_code == 0
    assert _posts() == [f"{BASE_URL}/Library/Refresh"]
    assert orjson.loads(result.output)["applied"] is True


@responses.activate
def test_scan_apply_with_library_refreshes_only_that_library() -> None:
    responses.post(f"{BASE_URL}/Items/lib-1/Refresh", status=204)

    result = _invoke("scan", "--library", "lib-1", "--apply")

    assert result.exit_code == 0
    assert _posts() == [f"{BASE_URL}/Items/lib-1/Refresh"]
    assert orjson.loads(result.output)["library"] == "lib-1"


@responses.activate
def test_maintenance_lists_tasks_when_no_task_is_named() -> None:
    responses.get(f"{BASE_URL}/ScheduledTasks", json=TASKS)

    result = _invoke("maintenance")

    assert result.exit_code == 0
    names = [task["name"] for task in orjson.loads(result.output)["tasks"]]
    assert names == ["Clean Cache", "Scan Media", "Refresh Metadata"]
    assert _posts() == []


@responses.activate
def test_maintenance_task_match_is_case_insensitive_on_key() -> None:
    responses.get(f"{BASE_URL}/ScheduledTasks", json=TASKS)

    result = _invoke("maintenance", "--task", "scanlibrary")

    assert result.exit_code == 0
    payload = orjson.loads(result.output)
    assert payload["id"] == "t-scan"
    assert payload["applied"] is False
    assert _posts() == []


@responses.activate
def test_maintenance_ambiguous_task_is_a_usage_error() -> None:
    duplicates = [{"Id": "a", "Name": "Scan"}, {"Id": "b", "Name": "SCAN"}]
    responses.get(f"{BASE_URL}/ScheduledTasks", json=duplicates)

    result = _invoke("maintenance", "--task", "scan")

    assert result.exit_code == 2
    assert 'Expected one task matching "scan", found 2' in result.output
    assert _posts() == []


@responses.activate
def test_maintenance_unknown_task_is_a_usage_error() -> None:
    responses.get(f"{BASE_URL}/ScheduledTasks", json=TASKS)

    result = _invoke("maintenance", "--task", "nope")

    assert result.exit_code == 2
    assert "found 0. Run --list" in result.output


@responses.activate
def test_maintenance_apply_starts_the_selected_task() -> None:
    responses.get(f"{BASE_URL}/ScheduledTasks", json=TASKS)
    responses.post(f"{BASE_URL}/ScheduledTasks/Running/t-clean", status=204)

    result = _invoke("maintenance", "--task", "t-clean", "--apply")

    assert result.exit_code == 0
    assert _posts() == [f"{BASE_URL}/ScheduledTasks/Running/t-clean"]
    assert orjson.loads(result.output)["message"] == 'Started "Clean Cache".'


@responses.activate
def test_sessions_stop_requires_confirmation() -> None:
    result = _invoke("sessions", "--stop", "s-1")

    assert result.exit_code == 2
    assert "requires --confirm" in result.output
    assert _posts() == []


@responses.activate
def test_sessions_stop_with_confirmation_sends_the_stop_command() -> None:
    responses.post(f"{BASE_URL}/Sessions/s-1/Playing/Stop", status=204)

    result = _invoke("sessions", "--stop", "s-1", "--confirm")

    assert result.exit_code == 0
    assert _posts() == [f"{BASE_URL}/Sessions/s-1/Playing/Stop"]


@responses.activate
def test_sessions_listing_summarises_playback() -> None:
    responses.get(
        f"{BASE_URL}/Sessions",
        json=[
            {
                "Id": "s-1",
                "UserName": "alice",
                "Client": "Web",
                "DeviceName": "laptop",
                "NowPlayingItem": {"Name": "Heat"},
                "TranscodingInfo": {"IsVideoDirect": False},
            },
            {"Id": "s-2", "UserName": "bob", "Client": "TV", "DeviceName": "tv"},
        ],
    )

    result = _invoke("sessions")

    assert result.exit_code == 0
    sessions = orjson.loads(result.output)["sessions"]
    assert sessions[0] == {
        "id": "s-1",
        "user": "alice",
        "client": "Web",
        "device": "laptop",
        "playing": "Heat",
        "transcoding": True,
    }
    assert sessions[1]["playing"] is None
    assert sessions[1]["transcoding"] is False


@responses.activate
def test_cleanup_dry_run_matches_cleanup_style_tasks_by_keyword() -> None:
    responses.get(f"{BASE_URL}/ScheduledTasks", json=TASKS)

    result = _invoke("cleanup")

    assert result.exit_code == 0
    payload = orjson.loads(result.output)
    assert payload["dry_run"] is True
    assert [task["id"] for task in payload["tasks"]] == ["t-clean", "t-meta"]
    assert _posts() == []


@responses.activate
def test_cleanup_apply_requires_confirmation() -> None:
    responses.get(f"{BASE_URL}/ScheduledTasks", json=TASKS)

    result = _invoke("cleanup", "--apply")

    assert result.exit_code == 2
    assert "requires --confirm" in result.output
    assert _posts() == []


@responses.activate
def test_cleanup_apply_with_confirmation_starts_each_matching_task() -> None:
    responses.get(f"{BASE_URL}/ScheduledTasks", json=TASKS)
    responses.post(f"{BASE_URL}/ScheduledTasks/Running/t-clean", status=204)
    responses.post(f"{BASE_URL}/ScheduledTasks/Running/t-meta", status=204)

    result = _invoke("cleanup", "--apply", "--confirm")

    assert result.exit_code == 0
    assert _posts() == [
        f"{BASE_URL}/ScheduledTasks/Running/t-clean",
        f"{BASE_URL}/ScheduledTasks/Running/t-meta",
    ]
    assert orjson.loads(result.output)["started"] == 2


@pytest.mark.parametrize("subcommand", ["status", "sessions", "maintenance"])
def test_server_commands_require_the_connection_options(subcommand: str) -> None:
    result = RUNNER.invoke(cli, ["server", subcommand])
    assert result.exit_code == 2
    assert "--server" in result.output or "--token" in result.output
