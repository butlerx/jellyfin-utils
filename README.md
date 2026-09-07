# jellyfin-utils

A collection of utility commands for managing a Jellyfin media server: finding
content that is safe to delete, reconciling Jellyseerr requests, and running
safe operational tasks against the server.

## Setup

Requires Python 3.12+. Uses [uv](https://docs.astral.sh/uv/) for dependency
management.

```bash
uv sync
```

### Environment Variables

All commands share the Jellyfin connection settings. `watched`, `stale`, and
`reclaim` can optionally use Jellyseerr for requester-watch prioritization;
`requests` and `user clone` require Jellyseerr:

| Variable            | Purpose                                        |
| ------------------- | ---------------------------------------------- |
| `JELLYFIN_SERVER`   | Base URL (e.g. `http://jellyfin.lan:8096`)     |
| `JELLYFIN_TOKEN`    | API key / token                                |
| `JELLYSEERR_SERVER` | Base URL (e.g. `http://jellyseerr.lan:5055`)   |
| `JELLYSEERR_TOKEN`  | API key; set together with `JELLYSEERR_SERVER` |

CLI flags override their matching environment variables. Set both Jellyseerr
variables to enable requester-watch prioritization without passing CLI flags. On
`watched`, `stale`, and `reclaim`, Jellyseerr is optional but the two flags are
all-or-nothing — passing one without the other is a usage error.

`--server` and `--token` are required on every command below and are omitted
from the per-command option lists to avoid repetition.

---

## Commands

All commands are available through the unified `jellyfin` CLI:

```bash
uv run jellyfin --help
uv run jellyfin --version
```

| Command              | Purpose                                              |
| -------------------- | ---------------------------------------------------- |
| `watched`            | Media most users have already seen                   |
| `stale`              | Media nobody (or almost nobody) has watched          |
| `reclaim`            | Combined cleanup queue: widely watched **and** stale |
| `duplicates`         | Items sharing a TMDb ID                              |
| `health`             | Library records unusable for storage analysis        |
| `requests`           | Jellyseerr requests vs. media present in Jellyfin    |
| `report`             | Library size and item-count summary                  |
| `user add`           | Create a Jellyfin user                               |
| `user clone`         | Clone a user's watch state, favorites, and playlists |
| `user verify-clone`  | Verify cloned watch state, favorites, and playlists  |
| `server status`      | Version, storage, failed tasks, active sessions      |
| `server scan`        | Preview or start a library scan                      |
| `server maintenance` | List or run built-in scheduled tasks                 |
| `server sessions`    | List sessions, or stop one                           |
| `server cleanup`     | Preview or run cleanup-oriented scheduled tasks      |

`jellyfin` is the only installed entry point; every command is a subcommand of
it.

## Output formats

Every command takes `--output [text|json|csv|markdown]` and defaults to `text`.

| Format     | Shape                                                   |
| ---------- | ------------------------------------------------------- |
| `text`     | Title, summary facts, then aligned tables — the default |
| `json`     | The full nested payload documented per command below    |
| `csv`      | The table rows only, with a header row                  |
| `markdown` | `#` heading, summary bullets, and Markdown tables       |

Notes that apply across formats:

- `json` is the only format that keeps nesting (duplicate groups, per-type maps,
  a task's full last-run result). The other three flatten to rows.
- In `csv`, a report with more than one table (`health`) precedes each table
  with a row naming it, and separates them with a blank line. Summary facts are
  omitted — use `json` when you want them.
- Commands that act on the server rather than list data (`server scan`,
  `server maintenance --task`, `server sessions --stop`,
  `server cleanup --apply`, `user add`, `user clone`, `user verify-clone`) have
  no table. Their `text` output leads with the same confirmation line as before,
  `csv` emits `Field,Value` pairs, and `json` carries the machine-readable
  result plus a `message` field.
- `--output json` is now explicit on the commands that used to print JSON with
  no flag (`duplicates`, `health`, `requests`, `report`, and the `server`
  commands), because `text` is the default everywhere.

---

## Command reference

Detailed options, examples, sample output, and JSON payload shapes live in the
[full command reference](docs/commands.md).

---

## Development

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- [prek](https://github.com/trufflesuite/prek) (optional, for pre-commit hooks)

### Setup

```bash
uv sync
```

This installs all runtime and dev dependencies and creates the virtualenv.

### Project Layout

```text
jellyfin_utils/
  cli.py              Unified `jellyfin` command group and composition root
  http.py             Shared request helpers; maps HTTP failures to CLI errors
  options.py          Shared Click option decorators
  output.py           Report/Table model and generic output renderers
  jellyseerr.py       Jellyseerr transport and user-management operations
  client/
    __init__.py       Public compatibility facade
    models.py         Library item model and pure display/size helpers
    transport.py      Jellyfin request operations
    pagination.py     Paginated item transport
    library.py        Library queries and transformations
    watch.py          Watch-state queries
  media/
    context.py        Shared watched/stale/reclaim data acquisition
    render.py         Shared media rendering primitives
  analysis/
    cli.py            Reclaim, duplicates, health, requests, and report commands
    render.py         Reclaim output formatting
  watched/            CLI, models, service, and renderer
  stale/              CLI, models, service, and renderer
  user/
    cli.py            User command definitions
    models.py         Clone request, snapshot, and result models
    snapshot.py       Pure snapshot normalization and comparison
    repository.py     Snapshot API reads and writes
    workflow.py       Clone and verification orchestration
    render.py         User command output formatting
    service.py        Compatibility facade for the former public module
  server/             Server-management CLI
tests/                pytest suite; HTTP is stubbed with `responses`
```

Architecture ownership rules:

- CLI modules translate Click options into calls and emit results; they do not
  own reusable domain logic.
- Services, workflows, and snapshots own reusable domain logic.
- Repositories and transport modules own API I/O.
- Renderers format prepared data and perform no API I/O.
- `jellyfin_utils/cli.py` is the application composition root;
  `client/__init__.py` is a compatibility facade over focused client modules.

Every API call goes through `http.py`, which turns timeouts, connection
failures, bad status codes, and non-JSON bodies into a one-line
`click.ClickException` rather than a traceback. Library listings are paginated
in `client.iter_items`, so a large library is never silently truncated.

### Adding a New Command

1. Create `jellyfin_utils/your_script/` with an `__init__.py` that exports
   `main`
2. Import shared client:
   `from jellyfin_utils.client import build_headers, get_users, ...`, then build
   a `Report` and call `emit(report, output_format)` from
   `jellyfin_utils.output` so the command supports every output format
3. Take every shared option from `jellyfin_utils.options` rather than declaring
   it by hand, and stack the decorators in the documented order:

   ```python
   @click.command("your-script")
   @connection_options          # --server/--token; you receive `base_url`
   @jellyseerr_options          # or @jellyseerr_options(required=True)
   # ...command-specific options...
   @output_option
   @quiet_option
   ```

   `options.py` also holds `ignore_user_option`, `threshold_option`, and
   `require_jellyseerr_pair`. Add any option a second command needs there

4. Register the command in `jellyfin_utils/cli.py`:

   ```python
   from jellyfin_utils.your_script.cli import main as your_script

   cli.add_command(your_script, "your-script")
   ```

5. Run `uv sync` to register the new command

### Testing

```bash
# Run the suite
uv run pytest

# One file, verbose
uv run pytest tests/test_client.py -v
```

Tests never touch a real server: `responses` stubs the HTTP layer, and
`click.testing.CliRunner` drives the commands. CI runs the suite on Python
3.12–3.14.

### Linting & Type Checking

```bash
# Lint (ruff with ALL rules enabled)
uv run ruff check jellyfin_utils/

# Auto-fix lint issues
uv run ruff check --fix jellyfin_utils/

# Type check
uv run ty check jellyfin_utils/

# Format
uv run ruff format jellyfin_utils/
```

### Pre-commit Hooks

[prek](https://github.com/trufflesuite/prek) runs ruff lint, ruff format,
trailing-whitespace, and other checks before each commit.

```bash
# Install the git hook
prek install

# Run all hooks manually
prek run --all-files
```

---

## License

[Apache License 2.0](LICENSE). See [NOTICE](NOTICE) for attribution.
