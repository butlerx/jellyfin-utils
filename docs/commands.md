# Command reference

Detailed options, examples, sample output, and JSON payload shapes for every
`jellyfin` command.

[← Back to the README](../README.md)

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
--jellyseerr-server TEXT  Jellyseerr URL (or JELLYSEERR_SERVER env var)
--jellyseerr-token TEXT   Jellyseerr API key (or JELLYSEERR_TOKEN env var)
--ignore-user TEXT        Username to ignore (repeatable)
--days INTEGER            Only count plays within the last N days as watched
--threshold INTEGER       Percentage of users who must have watched  [default: 80]
--output [text|json|csv|markdown]  Output format  [default: text]
--quiet                   Text mode: print only the item list, no summary
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
--jellyseerr-server TEXT  Jellyseerr URL (or JELLYSEERR_SERVER env var)
--jellyseerr-token TEXT   Jellyseerr API key (or JELLYSEERR_TOKEN env var)
--ignore-user TEXT        Username to ignore (repeatable)
--min-age INTEGER         Only flag items added more than N days ago
--max-watchers INTEGER    Max watchers for an item to count as stale  [default: 0]
--output [text|json|csv|markdown]  Output format  [default: text]
--quiet                   Text mode: print only the item list, no summary
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
--jellyseerr-server TEXT  Jellyseerr URL (or JELLYSEERR_SERVER env var)
--jellyseerr-token TEXT   Jellyseerr API key (or JELLYSEERR_TOKEN env var)
--ignore-user TEXT        Username to ignore (repeatable)
--threshold INTEGER       Percentage of users who must have watched  [default: 80]
--min-age INTEGER         Minimum stale-item age in days  [default: 90]
--output [text|json|csv|markdown]  Output format  [default: text]
--quiet                   Text mode: print only the item list, no summary
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
--snapshot PATH  JSON file to write with this report
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

### `jellyfin user clone SOURCE_USERNAME USERNAME --email EMAIL`

Creates a new Jellyfin user and linked Jellyseerr account, or safely resumes an
interrupted clone when `USERNAME` already exists. It copies played state, play
count, last-played timestamp, resume position, played percentage, favorites,
likes/ratings, and visible playlists. Each playlist becomes a private
destination-owned playlist with the same name and item order.

Before writing to Jellyfin, the command uses a read-only Jellyseerr request to
confirm that the API key has user-management permission. On a rerun it skips
matching item data and exact playlists, writes only missing or different state,
reuses the linked Jellyseerr account, and leaves the existing password
unchanged. It verifies the destination snapshot after applying Jellyfin data.
Network failures cannot be transactional across two servers, but rerunning after
a transient failure repairs and resumes partial work without duplicating exact
playlists. If Jellyfin does not persist a field, the command exits with an item
name, type, ID, and expected/actual field values instead of recommending another
identical rerun. Jellyseerr request ownership is always left unchanged.

#### Options

```text
--email TEXT              Required Jellyseerr email for the new user
--password TEXT           New Jellyfin password; prompts securely when omitted
--no-password             Create the new Jellyfin account without a password
--details-file FILE       Write complete verification differences as JSON
--jellyseerr-server TEXT  Jellyseerr base URL  [required]
--jellyseerr-token TEXT   Jellyseerr API key with user-management permission
--output [text|json|csv|markdown]  Output format  [default: text]
--help                    Show this message and exit
```

```bash
# Fresh clone: enter the new Jellyfin password securely when prompted
uv run jellyfin user clone old-name new-name --email new-name@example.com

# Passwordless fresh clone
uv run jellyfin user clone old-name new-name \
  --email new-name@example.com \
  --no-password

# Resume after a transient failure; no password prompt occurs
uv run jellyfin user clone old-name new-name --email new-name@example.com

# Save every verification difference when diagnosing a persistent failure
uv run jellyfin user clone old-name new-name \
  --email new-name@example.com \
  --details-file clone-differences.json
```

`JELLYFIN_SERVER`, `JELLYFIN_TOKEN`, `JELLYSEERR_SERVER`, and `JELLYSEERR_TOKEN`
can provide the four connection options.

### `jellyfin user verify-clone SOURCE_USERNAME USERNAME`

Read-only comparison of the source and destination Jellyfin users. It checks
every meaningful user-data record, including watched status, play count,
last-played timestamp, resume position, favorites, likes/ratings, and every
playlist's ordered item IDs. Folder and collection played state is excluded
because Jellyfin derives it from playable descendants; independently stored
folder favorites and ratings are still compared. The command deduplicates
unstable `/Items` pages and checks apparent destination misses through the
direct user-data endpoint. Text output shows a capped table with item names,
types, IDs, and expected/actual field values. JSON includes the same structured
details and diagnostic counts.

```bash
uv run jellyfin user verify-clone old-name new-name
uv run jellyfin user verify-clone old-name new-name --output json
uv run jellyfin user verify-clone old-name new-name \
  --details-file clone-differences.json
```

A successful comparison reports `Verified: yes`. A differing comparison is a
valid report rather than a command error, so automation should check the JSON
`verified` field.

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

```text
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

```text
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

```text
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

```text
--apply    Actually start selected maintenance tasks
--confirm  Required with --apply
--output [text|json|csv|markdown]  Output format  [default: text]
```

```bash
uv run jellyfin server cleanup                     # dry run: lists matching tasks
uv run jellyfin server cleanup --apply --confirm
```

---
