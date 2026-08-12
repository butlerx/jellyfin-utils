# jellyfin-utils

A collection of utility scripts for managing a Jellyfin media server.

## Setup

Requires Python 3.12+. Uses [uv](https://docs.astral.sh/uv/) for dependency
management.

```bash
uv sync
```

### Environment Variables

All scripts share the Jellyfin connection settings. The watched and stale
commands can also use Jellyseerr to prioritize content watched by the person who
requested it:

| Variable            | Purpose                                        |
| ------------------- | ---------------------------------------------- |
| `JELLYFIN_SERVER`   | Base URL (e.g. `http://jellyfin.lan:8096`)     |
| `JELLYFIN_TOKEN`    | API key / token                                |
| `JELLYSEERR_SERVER` | Base URL (e.g. `http://jellyseerr.lan:5055`)   |
| `JELLYSEERR_TOKEN`  | API key; set together with `JELLYSEERR_SERVER` |

CLI flags override their matching environment variables. Set both Jellyseerr
variables to enable requester-watch prioritization without passing CLI flags.

---

## Usage

All commands are available through the unified `jellyfin` CLI:

```bash
uv run jellyfin --help
uv run jellyfin watched [OPTIONS]
uv run jellyfin stale [OPTIONS]
uv run jellyfin user add USERNAME [OPTIONS]
uv run jellyfin reclaim [OPTIONS]
uv run jellyfin duplicates [OPTIONS]
uv run jellyfin health [OPTIONS]
uv run jellyfin requests [OPTIONS]
uv run jellyfin report [OPTIONS]
uv run jellyfin server COMMAND [OPTIONS]
```

Standalone entry points also work:

```bash
uv run jellyfin-watched [OPTIONS]
uv run jellyfin-stale [OPTIONS]
```

### `jellyfin user add`

Creates a Jellyfin user. By default, the command securely prompts for a password
and confirmation; use `--password` only for non-interactive automation. Creating
a passwordless account requires the explicit `--no-password` flag.

```bash
# Create a user and enter their password securely when prompted
uv run jellyfin user add alice

# Create a user from an automation script
uv run jellyfin user add alice --password "$JELLYFIN_NEW_USER_PASSWORD"

# Explicitly create a passwordless account
uv run jellyfin user add kiosk --no-password
```

```
--server TEXT   Jellyfin server URL (or JELLYFIN_SERVER env var)  [required]
--token TEXT    API key with user-management permission (or JELLYFIN_TOKEN env var)  [required]
--password TEXT User password; prompts securely when omitted
--no-password   Create an account without a password
--help          Show this message and exit
```

### Additional analysis commands

All analysis commands return JSON for scripting and review.

```bash
# One cleanup-review queue, combining widely watched and stale items
uv run jellyfin reclaim --min-age 90

# Duplicates with the same Jellyfin media type and TMDb ID
uv run jellyfin duplicates

# Items without an on-disk path or known file size
uv run jellyfin health

# Reconcile Jellyseerr requests with media present in Jellyfin
uv run jellyfin requests

# Produce a library-size summary and save a snapshot
uv run jellyfin report --snapshot .jellyfin-utils/report.json
```

### Server-management commands

Management commands never mutate the server unless their explicit action flag
is supplied. `cleanup` only runs installed Jellyfin scheduled tasks whose names
or descriptions identify them as cleanup-oriented; it does not delete files
itself.

```bash
# Server version, storage, failed scheduled tasks, and active sessions
uv run jellyfin server status

# Preview, then start a full library scan
uv run jellyfin server scan
uv run jellyfin server scan --apply

# List task IDs, then start one built-in scheduled task
uv run jellyfin server maintenance --list
uv run jellyfin server maintenance --task TASK_ID --apply

# List active sessions; stopping playback needs explicit confirmation
uv run jellyfin server sessions
uv run jellyfin server sessions --stop SESSION_ID --confirm

# Preview cleanup tasks; require two flags to run them
uv run jellyfin server cleanup
uv run jellyfin server cleanup --apply --confirm
```

---

## Scripts

### `jellyfin-watched`

Identifies movies and TV shows that have been **watched by all or most users** —
content everyone has already seen that can be safely removed to free up space.

- Configurable watch threshold (default 80% of users)
- Per-item watch stats: count, percentage, and which users watched
- Sorted by file size (largest first) to prioritize space savings
- Grouped by media type (Movies, Series, Episodes)
- Skips items already removed from disk
- Ignore specific users (e.g., guests, kids)
- Date filtering: only count recent plays
- Optionally prioritize Jellyseerr requests the requester has watched
- Output formats: text, Markdown, JSON, or CSV

#### Examples

```bash
# Basic usage (80% threshold by default)
uv run jellyfin-watched \
  --server http://jellyfin.lan:8096 \
  --token YOUR_API_KEY

# Only show content ALL users watched (100%)
uv run jellyfin-watched --threshold 100

# Show content 60%+ users watched
uv run jellyfin-watched --threshold 60

# Ignore guest accounts
uv run jellyfin-watched --ignore-user guest --ignore-user kid1

# Only count plays within the last year
uv run jellyfin-watched --days 365

# Prioritize Jellyseerr-requested content watched by its requester
export JELLYSEERR_SERVER=http://jellyseerr.lan:5055
export JELLYSEERR_TOKEN=YOUR_JELLYSEERR_API_KEY
uv run jellyfin-watched

# CSV for spreadsheet analysis
uv run jellyfin-watched --output csv > candidates.csv

# Markdown for a message, issue, or pull request
uv run jellyfin-watched --output markdown > candidates.md

# JSON for scripting
uv run jellyfin-watched --output json | jq '.candidates_count'

# Quiet mode (just the list, no summary)
uv run jellyfin-watched --quiet
```

#### Options

```
--server TEXT             Jellyfin server URL (or JELLYFIN_SERVER env var)  [required]
--token TEXT              API key (or JELLYFIN_TOKEN env var)  [required]
--ignore-user TEXT        Username to exclude from analysis (repeatable)
--days INTEGER            Only count plays within last N days
--threshold INTEGER       Percentage of users who must have watched (default: 80)
--jellyseerr-server TEXT  Jellyseerr URL (or JELLYSEERR_SERVER env var)
--jellyseerr-token TEXT   Jellyseerr API key (or JELLYSEERR_TOKEN env var)
--output [text|json|csv|markdown]  Output format (default: text)
--quiet                   Text mode: skip summary, show only candidate list
--help                    Show this message and exit
```

#### Sample Output

```
Total users: 5
Ignoring users: guest
Active users analyzed: 4
Watch threshold: 80% of users
Total library items scanned: 1234

Candidate items (watched by >=80% of users): 37
Total size of candidates: 145.67 GB

================================================================================
Movies (23 items)
================================================================================
The Matrix                                         | 4/4 users (100.0%) |  12.34 GB
  Watched by: alice, bob, charlie, dana
  ID: abc123def456
  Path: /media/movies/The Matrix (1999)/The Matrix.mkv

================================================================================
Episodes (6 items)
================================================================================
Breaking Bad - S01E01 - Pilot                      | 4/4 users (100.0%) |   3.21 GB
  Watched by: alice, bob, charlie, dana
  ID: episode789
  Path: /media/tv/Breaking Bad/Season 01/S01E01.mkv
```

---

### `jellyfin-stale`

Identifies movies and TV shows that **nobody (or almost nobody) has watched** —
content sitting on disk unused that could be removed to free up space.

- Configurable watcher threshold (default 0 = completely unwatched)
- Minimum age filter to skip recently-added content
- Per-item stats: watch count, percentage, age, and file size
- Sorted by file size (largest first) to prioritize space savings
- Grouped by media type (Movies, Series, Episodes)
- Skips items already removed from disk
- Ignore specific users (e.g., guests, kids)
- Optionally prioritizes Jellyseerr requests the requester has watched
- Output formats: text, Markdown, JSON, or CSV

#### Examples

```bash
# Basic usage (completely unwatched content)
uv run jellyfin-stale \
  --server http://jellyfin.lan:8096 \
  --token YOUR_API_KEY

# Include items watched by at most 1 user
uv run jellyfin-stale --max-watchers 1

# Only flag content older than 90 days (skip recent additions)
uv run jellyfin-stale --min-age 90

# Ignore guest accounts
uv run jellyfin-stale --ignore-user guest --ignore-user kid1

# Prioritize Jellyseerr-requested content watched by its requester
export JELLYSEERR_SERVER=http://jellyseerr.lan:5055
export JELLYSEERR_TOKEN=YOUR_JELLYSEERR_API_KEY
uv run jellyfin-stale

# CSV for spreadsheet analysis
uv run jellyfin-stale --output csv > stale.csv

# Markdown for a message, issue, or pull request
uv run jellyfin-stale --output markdown > stale.md

# JSON for scripting
uv run jellyfin-stale --output json | jq '.stale_count'

# Quiet mode (just the list, no summary)
uv run jellyfin-stale --quiet
```

#### Options

```
--server TEXT             Jellyfin server URL (or JELLYFIN_SERVER env var)  [required]
--token TEXT              API key (or JELLYFIN_TOKEN env var)  [required]
--ignore-user TEXT        Username to exclude from analysis (repeatable)
--min-age INTEGER         Only flag items added more than N days ago
--max-watchers INTEGER    Max users who watched for item to be stale (default: 0)
--jellyseerr-server TEXT  Jellyseerr URL (or JELLYSEERR_SERVER env var)
--jellyseerr-token TEXT   Jellyseerr API key (or JELLYSEERR_TOKEN env var)
--output [text|json|csv|markdown]  Output format (default: text)
--quiet                   Text mode: skip summary, show only stale list
--help                    Show this message and exit
```

#### Sample Output

```
Total users: 5
Ignoring users: guest
Active users analyzed: 4
Stale threshold: watched by <= 0 users
Total library items scanned: 1234

Stale items (watched by <=0 users): 89
Total size of stale content: 312.45 GB

================================================================================
Movies (52 items)
================================================================================
Some Unwatched Movie                               | 0/4 users (0.0%) |  15.67 GB | 245d old
  ID: abc123def456
  Path: /media/movies/Some Unwatched Movie (2020)/Some Unwatched Movie.mkv

================================================================================
Episodes (31 items)
================================================================================
Obscure Show - S01E01 - Pilot                      | 0/4 users (0.0%) |   2.10 GB | 180d old
  ID: episode789
  Path: /media/tv/Obscure Show/Season 01/S01E01.mkv
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

`client/` is the shared API layer — new scripts should import from here rather
than making raw Jellyfin API calls directly. Each script gets its own
sub-package (e.g. `watched/`) that keeps its models, logic, and rendering
private.

### Adding a New Script

1. Create `jellyfin_utils/your_script/` with an `__init__.py` that exports
   `main`
2. Import shared client:
   `from jellyfin_utils.client import build_headers, get_users, ...`
3. Register the command in `jellyfin_utils/cli.py`:
   ```python
   from jellyfin_utils.your_script.cli import main as your_script
   cli.add_command(your_script, "your-script")
   ```
4. Add entry points in `pyproject.toml`:
   ```toml
   [project.scripts]
   jellyfin = "jellyfin_utils.cli:cli"
   jellyfin-watched = "jellyfin_utils.watched:main"
   jellyfin-your-script = "jellyfin_utils.your_script:main"
   ```
5. Run `uv sync` to register the new command

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
