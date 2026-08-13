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

All commands share the Jellyfin connection settings. The `watched`, `stale`,
`reclaim`, and `requests` commands can also talk to Jellyseerr to prioritize
content watched by the person who requested it:

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
  `server cleanup --apply`, `user add`) have no table. Their `text` output leads
  with the same confirmation line as before, `csv` emits `Field,Value` pairs,
  and `json` carries the machine-readable result plus a `message` field.
- `--output json` is now explicit on the commands that used to print JSON with
  no flag (`duplicates`, `health`, `requests`, `report`, and the `server`
  commands), because `text` is the default everywhere.

---

## Analysis commands

Every analysis command works from the same library snapshot, which applies three
fixes before anything is reported:

- **Series with no episodes are dropped.** A series whose episodes were all
  deleted keeps its Jellyfin record — and usually its folder — so it would
  otherwise appear in every report as a zero-byte, never-watched item.
- **Series sizes total their episodes.** Jellyfin stores file sizes on episodes,
  never on the series record, so a series would otherwise always read `0.00 GB`.
- **Episodes are named `Series - S01E02 - Title`**, so a queue of episodes tells
  you what to delete without a second lookup.

Because a series size is the total of episodes that are also listed
individually, **series are left out of the reported totals** — otherwise every
episode would be counted twice. Per-item sizes are always shown.

### `jellyfin watched`

Identifies movies and TV shows **watched by all or most users** — content
everyone has already seen, which can be removed to free space.

- Configurable watch threshold (default 80% of active users)
- Per-item stats: watch count, percentage, and which users watched
- Sorted requester-watched first, then by file size (largest first)
- Grouped by media type (Movies, Series, Episodes)
- Skips items already removed from disk
- Ignore specific users (e.g. guests, kids), and optionally only count recent
  plays

#### Options

```
--ignore-user TEXT        Username to ignore (repeatable)
--days INTEGER            Only count plays within the last N days as "watched"
--threshold INTEGER       Percentage of users who must have watched  [default: 80]
--jellyseerr-server TEXT  Jellyseerr URL (or JELLYSEERR_SERVER env var)
--jellyseerr-token TEXT   Jellyseerr API key (or JELLYSEERR_TOKEN env var)
--output [text|json|csv|markdown]  Output format  [default: text]
--quiet                   Text mode: skip summary, show only the item list
--help                    Show this message and exit
```

#### Examples

```bash
# Basic usage (80% threshold by default)
uv run jellyfin watched --server http://jellyfin.lan:8096 --token YOUR_API_KEY

# Only content ALL users watched
uv run jellyfin watched --threshold 100

# Ignore guest accounts, and only count plays from the last year
uv run jellyfin watched --ignore-user guest --ignore-user kid1 --days 365

# Prioritize Jellyseerr-requested content watched by its requester
export JELLYSEERR_SERVER=http://jellyseerr.lan:5055
export JELLYSEERR_TOKEN=YOUR_JELLYSEERR_API_KEY
uv run jellyfin watched

# Other formats
uv run jellyfin watched --output csv > candidates.csv
uv run jellyfin watched --output markdown > candidates.md
uv run jellyfin watched --output json | jq '.candidates_count'
uv run jellyfin watched --quiet
```

#### Sample output

```
Total users: 5
Ignoring users: guest
Active users analyzed: 4
Watch threshold: 80% of users
Total library items scanned: 1234
PRIORITY = requested in Jellyseerr and watched by its requester

Candidate items (watched by >=80% of users): 37
Total size of candidates: 145.67 GB

================================================================================
Movies (23 items)
================================================================================
Priority | Title                                              | Watched        | Size
PRIORITY | The Matrix                                         | 4/4 users (100.0%) |  12.34 GB
  Watched by: alice, bob, charlie, dana
  Requested by: bob
  Requester watched: bob
  ID: abc123def456
  Path: /media/movies/The Matrix (1999)/The Matrix.mkv
```

JSON mode returns `server`, `total_users`, `ignored_users`, `active_users`,
`threshold_percent`, `total_items`, `max_age_days`,
`jellyseerr_requester_watch_prioritization`, `candidates_count`,
`candidates_by_type`, and a `candidates` array.

---

### `jellyfin stale`

Identifies movies and TV shows **nobody (or almost nobody) has watched** —
content sitting on disk unused.

- Configurable watcher threshold (default 0 = completely unwatched)
- Minimum age filter to skip recently-added content
- Per-item stats: watch count, percentage, age, and file size
- Sorted requester-watched first, then by file size (largest first)
- Grouped by media type, skipping items already removed from disk

#### Options

```
--ignore-user TEXT        Username to ignore (repeatable)
--min-age INTEGER         Only flag items added more than N days ago
--max-watchers INTEGER    Max watchers for an item to count as stale  [default: 0]
--jellyseerr-server TEXT  Jellyseerr URL (or JELLYSEERR_SERVER env var)
--jellyseerr-token TEXT   Jellyseerr API key (or JELLYSEERR_TOKEN env var)
--output [text|json|csv|markdown]  Output format  [default: text]
--quiet                   Text mode: skip summary, show only the item list
--help                    Show this message and exit
```

#### Examples

```bash
# Completely unwatched content
uv run jellyfin stale --server http://jellyfin.lan:8096 --token YOUR_API_KEY

# Include items watched by at most 1 user, ignoring recent additions
uv run jellyfin stale --max-watchers 1 --min-age 90

# Ignore guest accounts
uv run jellyfin stale --ignore-user guest --ignore-user kid1

# Prioritize Jellyseerr-requested content watched by its requester
export JELLYSEERR_SERVER=http://jellyseerr.lan:5055
export JELLYSEERR_TOKEN=YOUR_JELLYSEERR_API_KEY
uv run jellyfin stale

# Other formats
uv run jellyfin stale --output csv > stale.csv
uv run jellyfin stale --output markdown > stale.md
uv run jellyfin stale --output json | jq '.stale_count'
uv run jellyfin stale --quiet
```

#### Sample output

```
Total users: 5
Ignoring users: guest
Active users analyzed: 4
Stale threshold: watched by <= 0 users
Total library items scanned: 1234
Minimum age: 90 days (newer items excluded)

Stale items (watched by <=0 users): 89
Total size of stale content: 312.45 GB

================================================================================
Movies (52 items)
================================================================================
Priority | Title                                              | Watched        | Size      | Age
standard | Some Unwatched Movie                               | 0/4 users (0.0%) |  15.67 GB | 245d old
  ID: abc123def456
  Path: /media/movies/Some Unwatched Movie (2020)/Some Unwatched Movie.mkv
```

JSON mode returns `server`, `total_users`, `ignored_users`, `active_users`,
`max_watchers`, `min_age_days`, `jellyseerr_requester_watch_prioritization`,
`total_items`, `stale_count`, `stale_by_type`, and a `stale_items` array.

---

### `jellyfin reclaim`

One cleanup-review queue combining both signals: items most users have already
watched, and items nobody has watched. Overlapping items are merged and tagged
`widely_watched_and_stale`. Text output groups the queue by reason, best
candidates first; within each group, results are sorted requester-watched first,
then largest first.

#### Options

```
--ignore-user TEXT        Username to exclude (repeatable)
--threshold INTEGER       Watched percentage required  [default: 80]
--min-age INTEGER         Minimum stale-item age in days  [default: 90]
--jellyseerr-server TEXT  Jellyseerr URL (or JELLYSEERR_SERVER env var)
--jellyseerr-token TEXT   Jellyseerr API key (or JELLYSEERR_TOKEN env var)
--output [text|json|csv|markdown]  Output format  [default: text]
--quiet                   Text mode: skip summary, show only the item list
--help                    Show this message and exit
```

#### Examples

```bash
# Cleanup queue for items added more than 90 days ago
uv run jellyfin reclaim --min-age 90

# With Jellyseerr: requester-watched items rank first and requesters are listed
uv run jellyfin reclaim --min-age 90 \
  --jellyseerr-server http://jellyseerr.lan:5055 --jellyseerr-token "$JELLYSEERR_TOKEN"

# Other formats
uv run jellyfin reclaim --output markdown > reclaim.md
uv run jellyfin reclaim --output csv > reclaim.csv
uv run jellyfin reclaim --output json | jq '.estimated_reclaimable_gib'
uv run jellyfin reclaim --quiet
```

#### Sample output

```
Reclaim review · 112 candidates · 458.02 GiB to reclaim
Series sizes total their episodes, which are listed separately and counted once.
* = requested in Jellyseerr and already watched by whoever requested it

── Widely watched, and stale · 12 items · 145.67 GiB ───────────────────────────
* Movie   The Matrix                                             4 watchers   12.34 GiB
          requested by bob, who has since watched it
          /media/movies/The Matrix (1999)/The Matrix.mkv  [abc123def456]

── Stale · 74 items · 298.14 GiB ───────────────────────────────────────────────
  Series  Some Cancelled Show                                    0 watchers   45.10 GiB
          /media/tv/Some Cancelled Show  [ghi789]
  Episode Breaking Bad - S01E01 - Pilot                          0 watchers    3.21 GiB
          /media/tv/Breaking Bad/Season 01/S01E01.mkv  [def456]
```

`--quiet` prints the item lines only, with no header, rules, or detail lines.

JSON mode wraps the same entries:

```json
{
  "candidates": [
    {
      "reason": "stale",
      "item": "Breaking Bad - S01E01 - Pilot",
      "series": "Breaking Bad",
      "id": "def456",
      "type": "Episode",
      "path": "/media/tv/Breaking Bad/Season 01/S01E01.mkv",
      "size_gib": 3.21,
      "size_is_rollup": false,
      "watchers": 0,
      "requested_by": [],
      "watched_by_requester": [],
      "requester_watched": false
    }
  ],
  "count": 112,
  "estimated_reclaimable_gib": 458.02,
  "jellyseerr_enabled": true
}
```

`size_is_rollup` is `true` for series, whose size is the total of their
episodes; those entries are excluded from `estimated_reclaimable_gib`.

`reason` is one of `widely_watched`, `stale`, or `widely_watched_and_stale`.
`series` is `null` for movies and series, and set for episodes (CSV and Markdown
carry it as its own column). `jellyseerr_enabled` distinguishes "no requesters
found" from "Jellyseerr was not configured" — without it, `requested_by` is
always empty.

---

### `jellyfin duplicates`

Finds on-disk items that share a Jellyfin media type and TMDb ID — the same film
or show present more than once. Takes no options beyond the connection flags and
`--output`.

```bash
uv run jellyfin duplicates
uv run jellyfin duplicates --output json
```

```json
{
  "duplicate_groups": [
    {
      "type": "Movie",
      "tmdb_id": 603,
      "items": [
        {
          "id": "abc123",
          "name": "The Matrix",
          "year": 1999,
          "path": "/media/movies/The Matrix (1999)/The Matrix.mkv",
          "size_gib": 12.34
        },
        {
          "id": "def456",
          "name": "The Matrix",
          "year": 1999,
          "path": "/media/movies/Matrix/matrix-1080p.mkv",
          "size_gib": 8.1
        }
      ]
    }
  ],
  "count": 1
}
```

Items without a path or without a TMDb ID are skipped — see `health` for those.

---

### `jellyfin health`

Reports library records that cannot be used for storage analysis: items with no
on-disk path, and on-disk items reporting a zero file size. Run this first if
`watched`, `stale`, or `reclaim` totals look wrong. Takes no options beyond the
connection flags and `--output`.

```bash
uv run jellyfin health
uv run jellyfin health --output json
```

```json
{
  "items_scanned": 1234,
  "missing_path": [{ "id": "abc123", "name": "Some Movie", "type": "Movie" }],
  "zero_size": [
    { "id": "def456", "name": "Other Movie", "path": "/media/movies/other.mkv" }
  ]
}
```

Series are not expected here: their size is rolled up from their episodes before
this check runs, so a series only lands in `zero_size` when every one of its
episodes reports zero bytes too.

---

### `jellyfin requests`

Reconciles every Jellyseerr request against media currently in Jellyfin, so you
can spot requests that were approved but never landed. Jellyseerr is
**required** here, unlike the other commands.

#### Options

```
--jellyseerr-server TEXT  Jellyseerr server URL (or JELLYSEERR_SERVER env var)  [required]
--jellyseerr-token TEXT   Jellyseerr API key (or JELLYSEERR_TOKEN env var)  [required]
--output [text|json|csv|markdown]  Output format  [default: text]
--help                    Show this message and exit
```

```bash
uv run jellyfin requests

# Requests that never landed in the library
uv run jellyfin requests --output json |
  jq '.requests[] | select(.available_in_jellyfin == false)'
```

```json
{
  "requests": [
    {
      "id": 42,
      "status": 2,
      "requested_by": "bob",
      "tmdb_id": 603,
      "media_type": "movie",
      "available_in_jellyfin": true
    }
  ],
  "count": 137
}
```

`status` is Jellyseerr's own request-status code, passed through unchanged.

---

### `jellyfin report`

Summarizes library size and item counts by media type, optionally writing the
same JSON to a file so you can track growth over time.

#### Options

```
--snapshot PATH  Optional JSON file to write with this report
--output [text|json|csv|markdown]  Output format  [default: text]
--help           Show this message and exit
```

```bash
uv run jellyfin report
uv run jellyfin report --output json
uv run jellyfin report --snapshot .jellyfin-utils/report.json
```

`--snapshot` always writes JSON, whatever `--output` is set to.

```json
{
  "items": 1234,
  "total_gib": 4821.55,
  "by_type": { "Movie": { "items": 512, "bytes": 3102000000000 } }
}
```

`by_type` reports each type's own bytes, so the `Series` row repeats the bytes
already counted under `Episode`; `total_gib` counts them once and is the figure
to track. Parent directories for `--snapshot` are created if missing.

---

## User commands

### `jellyfin user add USERNAME`

Creates a Jellyfin user. By default the command securely prompts for a password
and confirmation; use `--password` only for non-interactive automation. Creating
a passwordless account requires the explicit `--no-password` flag. The command
fails if the username already exists (case-insensitive) or is empty, and
`--password` and `--no-password` cannot be combined.

The token needs user-management permission.

#### Options

```
--password TEXT  User password; prompts securely when omitted
--no-password    Create an account without a password instead of prompting
--output [text|json|csv|markdown]  Output format  [default: text]
--help           Show this message and exit
```

```bash
# Create a user and enter their password securely when prompted
uv run jellyfin user add alice

# Create a user from an automation script
uv run jellyfin user add alice --password "$JELLYFIN_NEW_USER_PASSWORD"

# Explicitly create a passwordless account
uv run jellyfin user add kiosk --no-password
```

---

## Server-management commands

Management commands never mutate the server unless their explicit action flag is
supplied. Destructive-adjacent operations need a second `--confirm` flag on top
of `--apply`.

### `jellyfin server status`

Server name and version, pending-restart state, library count, active-session
count, and the names of any scheduled tasks whose last run failed. Read-only;
takes no options beyond the connection flags and `--output`.

```bash
uv run jellyfin server status
uv run jellyfin server status --output json
```

```json
{
  "server": "jellyfin.lan",
  "version": "10.9.11",
  "pending_restart": false,
  "libraries": 4,
  "active_sessions": 2,
  "failed_tasks": ["Scan Media Library"]
}
```

### `jellyfin server scan`

Starts a library scan. Without `--apply` it only reports what it would do.

```
--library TEXT  Optional library ID to refresh; omit for all libraries
--apply         Actually start the scan
--output [text|json|csv|markdown]  Output format  [default: text]
```

```bash
uv run jellyfin server scan                    # dry run
uv run jellyfin server scan --apply            # scan every library
uv run jellyfin server scan --library LIB_ID --apply
```

### `jellyfin server maintenance`

Lists Jellyfin's built-in scheduled tasks, or starts one. With no `--task` (or
with `--list`) it prints every task's ID, name, key, state, and last result.

```
--list       List runnable scheduled tasks
--task TEXT  Exact scheduled-task ID, name, or key to start
--apply      Actually start the selected task
--output [text|json|csv|markdown]  Output format  [default: text]
```

```bash
uv run jellyfin server maintenance --list
uv run jellyfin server maintenance --task TASK_ID            # dry run
uv run jellyfin server maintenance --task TASK_ID --apply
```

`--task` must match exactly one task by ID, name, or key; anything else is a
usage error telling you to run `--list`.

### `jellyfin server sessions`

Lists sessions active within the last 5 minutes, or stops one. Stopping playback
needs `--confirm`.

```
--stop TEXT  Session ID to stop
--confirm    Required with --stop
--output [text|json|csv|markdown]  Output format  [default: text]
```

```bash
uv run jellyfin server sessions
uv run jellyfin server sessions --stop SESSION_ID --confirm
```

```json
{
  "sessions": [
    {
      "id": "sess123",
      "user": "alice",
      "client": "Jellyfin Web",
      "device": "Firefox",
      "playing": "The Matrix",
      "transcoding": true
    }
  ]
}
```

### `jellyfin server cleanup`

Finds installed scheduled tasks whose name or description looks cleanup-oriented
(matching `clean`, `cache`, `image`, `metadata`, or `optim`) and runs them. It
only triggers Jellyfin's own tasks — it never deletes files itself. Running them
needs both `--apply` and `--confirm`.

```
--apply    Actually start selected maintenance tasks
--confirm  Required with --apply
--output [text|json|csv|markdown]  Output format  [default: text]
```

```bash
uv run jellyfin server cleanup                     # dry run: lists matching tasks
uv run jellyfin server cleanup --apply --confirm
```

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

```
jellyfin_utils/
  cli.py         Unified `jellyfin` command group
  output.py      Shared --output option and text/json/csv/markdown renderers
  client/        Shared Jellyfin API layer + LibraryItem model
  jellyseerr.py  Read-only Jellyseerr request client
  analysis/      reclaim, duplicates, health, requests, report (+ reclaim renderers)
  watched/       models, analysis logic, renderers
  stale/         models, analysis logic, renderers
  user/          user-management commands
  server/        server-management commands
```

`client/` is the shared API layer — new commands should import from here rather
than making raw Jellyfin API calls directly. Each larger command gets its own
sub-package (e.g. `watched/`) that keeps its models, logic, and rendering
private; the smaller commands live together in `analysis/cli.py`, with the
reclaim renderers in `analysis/render.py`.

### Adding a New Command

1. Create `jellyfin_utils/your_script/` with an `__init__.py` that exports
   `main`
2. Import shared client:
   `from jellyfin_utils.client import build_headers, get_users, ...`, and add
   `@output_option` from `jellyfin_utils.output`, building a `Report` and
   calling `emit(report, output_format)` so the command supports every output
   format
3. Register the command in `jellyfin_utils/cli.py`:
   ```python
   from jellyfin_utils.your_script.cli import main as your_script
   cli.add_command(your_script, "your-script")
   ```
4. Run `uv sync` to register the new command

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
