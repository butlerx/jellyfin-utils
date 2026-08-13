"""End-to-end CLI behaviour: option wiring, validation, and error presentation."""

from __future__ import annotations

from typing import TYPE_CHECKING

import click
import orjson
import pytest
import requests
import responses
from click.testing import CliRunner
from conftest import BASE_URL, JELLYSEERR_URL, items_page

from jellyfin_utils.cli import cli

if TYPE_CHECKING:
    from collections.abc import Iterator

RUNNER = CliRunner()

# Commands that take the shared Jellyfin connection options.
CONNECTED_COMMANDS = [
    ["duplicates"],
    ["health"],
    ["report"],
    ["stale"],
    ["watched"],
    ["reclaim"],
    ["server", "status"],
    ["user", "add", "someone", "--no-password"],
]


@pytest.fixture(autouse=True)
def _no_ambient_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's real JELLYFIN_* environment out of the tests."""
    for name in ("JELLYFIN_SERVER", "JELLYFIN_TOKEN", "JELLYSEERR_SERVER", "JELLYSEERR_TOKEN"):
        monkeypatch.delenv(name, raising=False)


def test_cli_exposes_every_command() -> None:
    result = RUNNER.invoke(cli, ["--help"])
    assert result.exit_code == 0
    for name in (
        "watched",
        "stale",
        "user",
        "reclaim",
        "duplicates",
        "health",
        "requests",
        "report",
        "server",
    ):
        assert name in result.output


@pytest.mark.parametrize("command", CONNECTED_COMMANDS, ids=lambda c: " ".join(c))
def test_connection_options_are_required(command: list[str]) -> None:
    result = RUNNER.invoke(cli, command)
    assert result.exit_code == 2
    assert "--server" in result.output


@pytest.mark.parametrize("command", CONNECTED_COMMANDS, ids=lambda c: " ".join(c))
def test_connection_options_come_from_the_environment(
    command: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JELLYFIN_SERVER", BASE_URL)
    monkeypatch.setenv("JELLYFIN_TOKEN", "env-token")
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mocked:
        mocked.add_passthru("http://")  # any call proves the options resolved
        mocked.get(f"{BASE_URL}/Items", json=items_page([], total=0))
        mocked.get(f"{BASE_URL}/Users", json=[])
        mocked.get(f"{BASE_URL}/System/Info", json={})
        mocked.get(f"{BASE_URL}/System/Info/Storage", json={"Libraries": []})
        mocked.get(f"{BASE_URL}/ScheduledTasks", json=[])
        mocked.get(f"{BASE_URL}/Sessions", json=[])
        mocked.post(f"{BASE_URL}/Users/New", json={"Id": "new", "Name": "someone"})
        result = RUNNER.invoke(cli, command)
    assert result.exit_code == 0, result.output


@responses.activate
def test_trailing_slash_on_the_server_url_is_stripped() -> None:
    responses.get(f"{BASE_URL}/Items", json=items_page([], total=0))
    result = RUNNER.invoke(cli, ["health", "--server", f"{BASE_URL}/", "--token", "t"])
    assert result.exit_code == 0, result.output
    # Without normalisation this would request "//Items".
    assert str(responses.calls[0].request.url).startswith(f"{BASE_URL}/Items")


@responses.activate
def test_repeated_trailing_slashes_are_stripped() -> None:
    responses.get(f"{BASE_URL}/Items", json=items_page([], total=0))
    result = RUNNER.invoke(cli, ["health", "--server", f"{BASE_URL}///", "--token", "t"])
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("command", ["stale", "watched", "reclaim"])
@pytest.mark.parametrize(
    "jellyseerr_args",
    [["--jellyseerr-server", JELLYSEERR_URL], ["--jellyseerr-token", "key"]],
    ids=["server-only", "token-only"],
)
def test_half_configured_jellyseerr_is_rejected(command: str, jellyseerr_args: list[str]) -> None:
    result = RUNNER.invoke(cli, [command, "--server", BASE_URL, "--token", "t", *jellyseerr_args])
    assert result.exit_code == 2
    assert "must be used together" in result.output


def test_requests_command_demands_both_jellyseerr_options() -> None:
    result = RUNNER.invoke(cli, ["requests", "--server", BASE_URL, "--token", "t"])
    assert result.exit_code == 2
    assert "--jellyseerr-server" in result.output


@responses.activate
def test_http_failure_prints_one_line_and_exits_one() -> None:
    responses.get(f"{BASE_URL}/Items", status=401)
    result = RUNNER.invoke(cli, ["health", "--server", BASE_URL, "--token", "bad"])
    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "Error: Jellyfin returned 401" in result.output
    assert "Traceback" not in result.output


@responses.activate
def test_unreachable_server_prints_one_line_and_exits_one() -> None:
    responses.get(f"{BASE_URL}/Users", body=requests.ConnectionError("refused"))
    result = RUNNER.invoke(cli, ["user", "add", "bob", "--no-password", "--server", BASE_URL, "--token", "t"])
    assert result.exit_code == 1
    assert "Could not connect to Jellyfin" in result.output
    assert "Traceback" not in result.output


@responses.activate
def test_health_reports_unusable_records_as_json() -> None:
    page = [
        {"Id": "ok", "Name": "Fine", "Type": "Movie", "Path": "/m/f.mkv", "MediaSources": [{"Size": 10}]},
        {"Id": "nopath", "Name": "No path", "Type": "Movie"},
        {"Id": "zero", "Name": "Zero", "Type": "Movie", "Path": "/m/z.mkv"},
    ]
    responses.get(f"{BASE_URL}/Items", json=items_page(page, total=3))

    result = RUNNER.invoke(cli, ["health", "--server", BASE_URL, "--token", "t", "--output", "json"])

    assert result.exit_code == 0, result.output
    payload = orjson.loads(result.output)
    assert payload["items_scanned"] == 3
    assert [item["id"] for item in payload["missing_path"]] == ["nopath"]
    assert [item["id"] for item in payload["zero_size"]] == ["zero"]


@responses.activate
def test_duplicates_groups_items_sharing_a_tmdb_id() -> None:
    page = [
        {"Id": "a", "Name": "Heat", "Type": "Movie", "Path": "/m/a.mkv", "ProviderIds": {"Tmdb": "949"}},
        {
            "Id": "b",
            "Name": "Heat 1080p",
            "Type": "Movie",
            "Path": "/m/b.mkv",
            "ProviderIds": {"Tmdb": "949"},
        },
        {"Id": "c", "Name": "Other", "Type": "Movie", "Path": "/m/c.mkv", "ProviderIds": {"Tmdb": "12"}},
    ]
    responses.get(f"{BASE_URL}/Items", json=items_page(page, total=3))

    result = RUNNER.invoke(cli, ["duplicates", "--server", BASE_URL, "--token", "t", "--output", "json"])

    assert result.exit_code == 0, result.output
    groups = orjson.loads(result.output)["duplicate_groups"]
    assert len(groups) == 1
    assert groups[0]["tmdb_id"] == 949


@pytest.mark.parametrize("username", ["", "   "])
def test_user_add_rejects_a_blank_username(username: str) -> None:
    result = RUNNER.invoke(
        cli,
        ["user", "add", username, "--no-password", "--server", BASE_URL, "--token", "t"],
    )
    assert result.exit_code == 2
    assert "cannot be empty" in result.output


def test_user_add_rejects_password_with_no_password() -> None:
    args = ["user", "add", "bob", "--password", "hunter2", "--no-password"]
    result = RUNNER.invoke(cli, [*args, "--server", BASE_URL, "--token", "t"], input="hunter2\n")
    assert result.exit_code == 2
    assert "cannot be used together" in result.output


@responses.activate
def test_user_add_rejects_an_existing_name_case_insensitively() -> None:
    responses.get(f"{BASE_URL}/Users", json=[{"Id": "u1", "Name": "Bob"}])
    result = RUNNER.invoke(cli, ["user", "add", "bob", "--no-password", "--server", BASE_URL, "--token", "t"])
    assert result.exit_code == 2
    assert "already exists" in result.output


@responses.activate
def test_user_add_creates_the_account_and_reports_it() -> None:
    responses.get(f"{BASE_URL}/Users", json=[])
    responses.post(f"{BASE_URL}/Users/New", json={"Id": "u9", "Name": "bob"})

    result = RUNNER.invoke(
        cli,
        ["user", "add", "bob", "--no-password", "--server", BASE_URL, "--token", "t", "--output", "json"],
    )

    assert result.exit_code == 0, result.output
    payload = orjson.loads(result.output)
    assert payload == {
        "created": True,
        "username": "bob",
        "id": "u9",
        "password_set": False,
        "message": 'Created user "bob" (ID: u9).',
    }
    body = responses.calls[1].request.body
    assert body is not None
    assert orjson.loads(body) == {"Name": "bob", "Password": None}


@responses.activate
def test_server_session_stop_requires_confirm() -> None:
    result = RUNNER.invoke(
        cli,
        ["server", "sessions", "--server", BASE_URL, "--token", "t", "--stop", "abc"],
    )
    assert result.exit_code == 2
    assert "--confirm" in result.output
    assert not responses.calls


@responses.activate
def test_server_cleanup_requires_confirm_before_running_tasks() -> None:
    responses.get(f"{BASE_URL}/ScheduledTasks", json=[])
    result = RUNNER.invoke(cli, ["server", "cleanup", "--server", BASE_URL, "--token", "t", "--apply"])
    assert result.exit_code == 2
    assert "--confirm" in result.output
    assert not any(call.request.method == "POST" for call in responses.calls)


def _walk(command: click.Command, prefix: tuple[str, ...] = ()) -> Iterator[tuple[str, click.Command]]:
    """Yield every leaf command in the tree, keyed by its full invocation path."""
    if isinstance(command, click.Group):
        for name, sub in command.commands.items():
            yield from _walk(sub, (*prefix, name))
    else:
        yield " ".join(prefix), command


ALL_COMMANDS = dict(_walk(cli))


def _flags(command: click.Command) -> list[str]:
    return [param.opts[0] for param in command.params if isinstance(param, click.Option)]


def test_every_command_was_discovered() -> None:
    assert set(ALL_COMMANDS) == {
        "duplicates",
        "health",
        "reclaim",
        "report",
        "requests",
        "server cleanup",
        "server maintenance",
        "server scan",
        "server sessions",
        "server status",
        "stale",
        "user add",
        "watched",
    }


@pytest.mark.parametrize("name", sorted(ALL_COMMANDS))
def test_connection_options_come_first(name: str) -> None:
    assert _flags(ALL_COMMANDS[name])[:2] == ["--server", "--token"]


@pytest.mark.parametrize("name", sorted(ALL_COMMANDS))
def test_jellyseerr_pair_follows_the_connection_options(name: str) -> None:
    flags = _flags(ALL_COMMANDS[name])
    if "--jellyseerr-server" not in flags:
        pytest.skip("command does not talk to Jellyseerr")
    assert flags[2:4] == ["--jellyseerr-server", "--jellyseerr-token"]


@pytest.mark.parametrize("name", sorted(ALL_COMMANDS))
def test_output_is_the_last_option_unless_quiet_follows_it(name: str) -> None:
    flags = _flags(ALL_COMMANDS[name])
    expected = ["--output", "--quiet"] if "--quiet" in flags else ["--output"]
    assert flags[-len(expected) :] == expected


@pytest.mark.parametrize("flag", ["--server", "--token", "--ignore-user", "--output", "--quiet"])
def test_shared_options_are_identical_everywhere(flag: str) -> None:
    seen = {
        param.help
        for command in ALL_COMMANDS.values()
        for param in command.params
        if isinstance(param, click.Option) and param.opts[0] == flag
    }
    assert len(seen) == 1, f"{flag} has divergent help text: {seen}"
