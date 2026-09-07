# Maintainability TODO

## Goal

Reduce duplication and mixed responsibilities without changing command names,
CLI flags, HTTP behavior, exit codes, or serialized output.

Estimated total effort: **4–6 engineering days**.

## Current baseline

- [x] `uv run pytest --cov` passes: **183 passed, 10 skipped; 89.05% coverage**.
- [x] `uv run ruff check jellyfin_utils/ tests/` passes.
- [x] `uv run ty check jellyfin_utils/` passes.
- [x] CI runs repository hooks and pytest on Python 3.12, 3.13, and 3.14.
- [x] Initial branch-coverage baseline recorded: **75.51%**; enforced floor:
      **75%**.

## Progress

| Phase | Outcome                                           |   Estimate | Depends on | Status      |
| ----- | ------------------------------------------------- | ---------: | ---------- | ----------- |
| 1     | Characterization tests and coverage gate          |  4–6 hours | —          | Complete    |
| 2     | User-clone workflow separated by responsibility   | 1.5–2 days | Phase 1    | Not started |
| 3     | Shared watched/stale/reclaim pipeline             | 1.5–2 days | Phase 1    | Not started |
| 4     | Jellyfin client split behind compatibility façade |  6–8 hours | Phase 1    | Not started |
| 5     | Architecture boundaries documented and enforced   |  3–4 hours | Phases 2–4 | Not started |

## Rules for every phase

- [ ] Keep each phase independently reviewable and releasable.
- [ ] Add or strengthen tests before moving behavior.
- [ ] Preserve public CLI behavior and output byte-for-byte unless a separate
      change explicitly approves a break.
- [ ] Avoid introducing abstractions until at least two concrete callers need
      them.
- [ ] Run the full validation gate before marking a phase complete.

### Full validation gate

```bash
uv run pytest
uv run ruff check jellyfin_utils/ tests/
uv run ruff format --check jellyfin_utils/ tests/
uv run ty check jellyfin_utils/
prek run --all-files
```

---

## Phase 1 — Protect current behavior

**Objective:** make later structural changes safe by capturing existing output
and establishing a non-regression coverage baseline.

**Estimate:** 4–6 hours.

### Files

- Modify `pyproject.toml`.
- Modify `.github/workflows/ci.yml` only if pytest configuration does not
  enforce coverage itself.
- Create `tests/test_watched.py`.
- Create `tests/test_stale.py`.
- Create `tests/test_reclaim.py`.
- Reuse fixtures from `tests/conftest.py`; do not duplicate HTTP fixture setup.

### 1.1 Add coverage tooling

- [x] Add `pytest-cov` to the `test` dependency group in `pyproject.toml`.
- [x] Regenerate `uv.lock`.
- [x] Run coverage once without a failure threshold:

  ```bash
  uv run pytest --cov=jellyfin_utils --cov-branch --cov-report=term-missing
  ```

- [x] Record total coverage and the least-covered production modules in this
      section.
- [x] Set `fail_under` to the measured total rounded down to a stable whole
      percentage.
- [x] Enable branch coverage.
- [x] Run the coverage gate in CI with `uv run pytest --cov`; keep targeted
      local test runs free from the project-wide threshold.

#### Coverage baseline

- Initial measured total: **75.51% branch coverage**.
- Coverage after characterization tests: **89.05%**.
- Initial enforced threshold: **75%**.
- Lowest-covered modules: `stale/render.py` (41%), `watched/render.py` (42%),
  `analysis/render.py` (43%), `jellyseerr.py` (50%), and `server/cli.py` (56%).

### 1.2 Characterize `watched`

- [x] Test the command through `click.testing.CliRunner`.
- [x] Freeze exact text output, including headings, spacing, ordering, and the
      trailing newline.
- [x] Freeze exact Markdown and CSV output without adding a snapshot-test
      dependency.
- [x] Assert the complete JSON object rather than checking only selected keys.
- [x] Cover `--quiet` output.
- [x] Cover no matching media.
- [x] Cover ignored users and active-user counts.
- [x] Cover `--days` filtering.
- [x] Cover requester-priority fields when Jellyseerr is configured.
- [x] Cover series roll-up behavior and `size_is_rollup`.

### 1.3 Characterize `stale`

- [x] Test text, Markdown, CSV, and JSON through `CliRunner`.
- [x] Cover no matching media and `--quiet`.
- [x] Cover `--min-age` boundaries.
- [x] Cover `--max-watchers` boundaries.
- [x] Cover ignored users and active-user counts.
- [x] Cover requester-priority fields when Jellyseerr is configured.
- [x] Assert ordering when multiple items have equal or different sizes.

### 1.4 Characterize `reclaim`

- [x] Test items classified as `widely_watched`.
- [x] Test items classified as `stale`.
- [x] Test items classified as `widely_watched_and_stale`.
- [x] Cover requester-first and size-descending ordering.
- [x] Freeze text, Markdown, CSV, and JSON output.
- [x] Cover empty results and `--quiet`.

### Phase 1 acceptance criteria

- [x] All previous tests still pass.
- [x] New tests protect every output format for watched, stale, and reclaim.
- [x] CI fails if coverage drops below the recorded baseline.
- [x] No runtime dependency was added for testing-only functionality.
- [x] The full validation gate passes on a clean checkout.

---

## Phase 2 — Separate the user-clone workflow

**Objective:** make user cloning understandable and testable without routing
every concern through `jellyfin_utils/user/cli.py` and
`jellyfin_utils/user/service.py`.

**Estimate:** 1.5–2 days.

**Dependency:** Phase 1 must be complete.

### Target structure

```text
jellyfin_utils/user/
  __init__.py
  cli.py          Click commands and option-to-request translation
  models.py       Immutable snapshot, difference, count, and outcome types
  snapshot.py     Pure normalization, matching, and verification
  repository.py   Jellyfin snapshot reads and writes
  workflow.py     Clone and verify orchestration
  render.py       Reports, payloads, and details-file serialization
  service.py      Temporary compatibility re-exports
```

### 2.1 Add focused tests before moving code

- [ ] Create `tests/test_user_snapshot.py` for pure snapshot comparison
      behavior.
- [ ] Create `tests/test_user_workflow.py` for orchestration and failure paths.
- [ ] Keep end-to-end CLI contracts in `tests/test_user_clone.py`.
- [ ] Cover duplicate source rows, missing destination items, mismatched fields,
      and missing playlists.
- [ ] Cover new destination users and resumed existing users.
- [ ] Cover failed verification and its exit code.
- [ ] Assert Jellyfin and Jellyseerr request order where order affects behavior.
- [ ] Assert the details-file schema and output-format behavior.

### 2.2 Extract immutable models

Move these types from `jellyfin_utils/user/service.py` to
`jellyfin_utils/user/models.py`:

- [ ] `PlaylistSnapshot`.
- [ ] `UserCloneSnapshot`.
- [ ] `CloneCounts`.
- [ ] `ItemDifference`.
- [ ] `VerificationResult`.

Add workflow boundary types:

```python
@dataclass(frozen=True)
class CloneUserRequest:
    source_username: str
    destination_username: str
    email: str
    base_url: str
    token: str = field(repr=False)
    jellyseerr_server: str = ""
    jellyseerr_token: str = field(default="", repr=False)
    password: str | None = field(default=None, repr=False)
    no_password: bool = False


@dataclass(frozen=True)
class CloneUserOutcome:
    source_name: str
    destination_name: str
    destination_user_id: str
    jellyseerr_user_id: int
    email: str
    destination_created: bool
    password_set: bool | None
    counts: CloneCounts
    verification: VerificationResult
```

- [ ] Confirm the final fields against the existing `_emit_clone_report` inputs
      before implementation.
- [ ] Mark tokens and passwords with `repr=False`.
- [ ] Keep models independent of Click, requests, and filesystem code.

### 2.3 Extract pure snapshot logic

Move pure behavior to `jellyfin_utils/user/snapshot.py`:

- [ ] `_positive_number`.
- [ ] `_has_meaningful_user_data` and `_has_watch_data`.
- [ ] `_copyable_user_data` and normalization helpers.
- [ ] User-data and playlist comparison helpers.
- [ ] `verify_user_snapshot`.

Requirements:

- [ ] No HTTP imports.
- [ ] No Click imports.
- [ ] No filesystem access.
- [ ] Tests construct model objects directly without response mocks.

### 2.4 Extract Jellyfin persistence

Move API-facing behavior to `jellyfin_utils/user/repository.py`:

- [ ] `_iter_playlist_items`.
- [ ] `capture_user_snapshot`.
- [ ] `_fetch_destination_user_data`.
- [ ] `resolve_destination_snapshot`.
- [ ] `apply_user_snapshot`.

Requirements:

- [ ] Preserve existing endpoint paths, parameters, request bodies, and call
      order.
- [ ] Keep HTTP failures flowing through the existing `http.py` behavior.
- [ ] Do not introduce a repository protocol or abstract base class.
- [ ] Keep snapshot comparison delegated to `snapshot.py`.

### 2.5 Extract orchestration

Create `jellyfin_utils/user/workflow.py`:

- [ ] Move destination discovery and Jellyseerr coordination out of the Click
      callback.
- [ ] Implement one orchestration entry point, such as
      `clone_user(request: CloneUserRequest) -> CloneUserOutcome`.
- [ ] Keep input normalization and Click-specific usage errors in `cli.py`
      unless they are valid outside the CLI.
- [ ] Return structured outcomes; do not print from the workflow.
- [ ] Preserve the rule that Jellyseerr updates occur only after Jellyfin
      verification succeeds.
- [ ] Preserve resume behavior for an existing destination user.
- [ ] Preserve the current failure report before exit code 1.

### 2.6 Extract presentation

Move presentation helpers from `jellyfin_utils/user/cli.py` to
`jellyfin_utils/user/render.py`:

- [ ] Item-difference payload construction.
- [ ] Verification payload construction.
- [ ] Verification tables.
- [ ] Details-file serialization.
- [ ] Clone and verification report construction/emission.

Requirements:

- [ ] Keep all current JSON keys and nesting.
- [ ] Keep text, Markdown, and CSV output stable.
- [ ] Keep path handling behavior unchanged in this refactor.
- [ ] Do not perform Jellyfin or Jellyseerr calls from the render module.

### 2.7 Reduce `cli.py` and preserve compatibility

- [ ] Keep Click decorators, command names, argument names, defaults, and option
      ordering unchanged.
- [ ] Reduce `clone_user` to validation, request construction, one workflow
      call, and rendering.
- [ ] Reduce `verify_clone` to input resolution, workflow/service invocation,
      and rendering.
- [ ] Re-export moved service symbols from `service.py` temporarily if tests or
      callers import them.
- [ ] Add removal notes for compatibility re-exports only after searching
      downstream usage.

### Phase 2 acceptance criteria

- [ ] `clone_user` is approximately 30 lines or fewer excluding its signature
      and decorators.
- [ ] No user-workflow production function exceeds roughly 60 lines or
      complexity 10.
- [ ] Pure snapshot tests run without HTTP mocks.
- [ ] Existing HTTP requests and serialized outputs remain unchanged.
- [ ] Tokens and passwords do not appear in model representations.
- [ ] The full validation gate passes.

---

## Phase 3 — Consolidate watched, stale, and reclaim

**Objective:** remove duplicated acquisition and rendering mechanics while
keeping watched and stale as distinct domain concepts.

**Estimate:** 1.5–2 days.

**Dependency:** Phase 1 must be complete. Phase 2 may run independently.

### Target structure

```text
jellyfin_utils/media/
  __init__.py
  context.py      Shared media-analysis data acquisition
  render.py       Shared output mechanics and summary data
jellyfin_utils/watched/
  cli.py
  models.py
  service.py      Candidate selection
  render.py       Watched-specific output adapter
jellyfin_utils/stale/
  cli.py
  models.py
  service.py      Stale-item selection
  render.py       Stale-specific output adapter
```

### 3.1 Define shared context types

Create `jellyfin_utils/media/context.py` with an immutable context similar to:

```python
@dataclass(frozen=True)
class MediaAnalysisContext:
    users: tuple[dict, ...]
    items: tuple[LibraryItem, ...]
    ignored_usernames: frozenset[str]
    active_user_count: int
    watchers: Mapping[str, tuple[str, ...]]
    requesters_by_tmdb_id: Mapping[int, tuple[str, ...]] | None
    base_url: str
```

- [ ] Prefer immutable collection types at the boundary where practical.
- [ ] Add derived properties only for values used by multiple commands.
- [ ] Do not put watched/stale thresholds into the shared context.

### 3.2 Extract the shared loading pipeline

Implement `load_media_analysis(...)` to own repeated setup:

- [ ] Validate the Jellyseerr server/token pair.
- [ ] Build Jellyfin headers.
- [ ] Fetch users.
- [ ] Normalize ignored usernames and calculate active-user count.
- [ ] Fetch watcher data using `max_watch_age_days`.
- [ ] Fetch all library items.
- [ ] Fetch requester mappings only when Jellyseerr is configured.

Call semantics:

- `watched` passes its `--days` value as `max_watch_age_days`.
- `stale` passes `None`.
- `reclaim` passes `None` so its current behavior remains unchanged.

### 3.3 Move domain analysis out of CLI modules

- [ ] Move `_make_candidate` and `find_candidates` to
      `jellyfin_utils/watched/service.py`.
- [ ] Move `find_stale` to `jellyfin_utils/stale/service.py`.
- [ ] Keep `Candidate` and `StaleItem` as separate types.
- [ ] Move shared grouping logic only if both features have exactly the same
      semantics.
- [ ] Update `analysis/cli.py` so `reclaim` imports service functions, never CLI
      modules.

### 3.4 Replace renderer parameter lists with summary data

Create a shared summary type in `jellyfin_utils/media/render.py`:

```python
@dataclass(frozen=True)
class MediaReportSummary:
    base_url: str
    total_users: int
    ignored_usernames: frozenset[str]
    active_user_count: int
    total_items: int
    jellyseerr_enabled: bool
```

- [ ] Pass feature criteria separately: watched threshold/age or stale
      watcher/age limits.
- [ ] Extract common CSV writing, Markdown escaping, grouping, and text-section
      framing only after confirming identical behavior.
- [ ] Keep watched-specific labels and fields in `watched/render.py`.
- [ ] Keep stale-specific labels and fields in `stale/render.py`.
- [ ] Do not force these commands onto `output.Report` if that changes quiet or
      compact output.

### 3.5 Simplify command entry points

Each watched/stale CLI command should perform only these actions:

1. Build a shared analysis context.
2. Call its feature service.
3. Build feature-specific render data.
4. Emit the selected format.

- [ ] Remove duplicated user/item/requester loading.
- [ ] Remove output-format `match` blocks from command functions by moving
      dispatch into rendering.
- [ ] Keep Click signatures and decorators unchanged.

### 3.6 Simplify reclaim mapping

- [ ] Extract candidate-to-reclaim-row conversion from `analysis/cli.py`.
- [ ] Extract stale-item-to-reclaim-row conversion.
- [ ] Use a typed reclaim row instead of incrementally assembled
      `dict[str, object]` where it improves clarity without changing JSON.
- [ ] Keep the three existing reason values unchanged.
- [ ] Preserve requester-first, size-descending ordering.

### Phase 3 acceptance criteria

- [ ] Phase 1 output contracts remain byte-compatible.
- [ ] Watched, stale, and reclaim use one shared context loader.
- [ ] Feature CLI modules do not perform format-specific rendering.
- [ ] `analysis/cli.py` does not import `watched.cli` or `stale.cli`.
- [ ] Watched/stale renderer duplication is materially reduced without hiding
      feature differences.
- [ ] The full validation gate passes.

---

## Phase 4 — Split the Jellyfin client

**Objective:** give transport, pagination, library mapping, and watch
aggregation separate owners while preserving the current import API.

**Estimate:** 6–8 hours.

**Dependency:** Phase 1 must be complete. This may run independently of Phases 2
and 3 after their active changes are merged.

### Target structure

```text
jellyfin_utils/client/
  __init__.py     Compatibility façade and documented public exports
  models.py       Existing LibraryItem model
  transport.py    Header construction and direct API operations
  pagination.py   Generic item pagination
  library.py      Item transformation and series aggregation
  watch.py        Watcher and watch-count aggregation
```

### 4.1 Add façade contract tests

- [ ] Add a test that imports every currently supported name from
      `jellyfin_utils.client`.
- [ ] Keep existing `tests/test_client.py` behavior tests unchanged before
      moving code.
- [ ] Add focused tests for page boundaries and empty pages if they are not
      already present.
- [ ] Add focused tests for series roll-up and ignored-user handling if missing.

### 4.2 Move transport operations

Move to `client/transport.py`:

- [ ] `build_headers`.
- [ ] `get_users`.
- [ ] `create_user`.
- [ ] `get_json`.
- [ ] `post_empty`.

Keep all requests routed through `jellyfin_utils/http.py`.

### 4.3 Move pagination

Move to `client/pagination.py`:

- [ ] `PAGE_SIZE`.
- [ ] `iter_items`.
- [ ] Pagination-only helpers.

Requirements:

- [ ] Preserve request parameters and stopping rules.
- [ ] Keep pagination generic; do not add library-specific transformation.

### 4.4 Move library behavior

Move to `client/library.py`:

- [ ] `parse_last_played`.
- [ ] `_is_played_recently` if it remains library-owned after the split.
- [ ] `drop_empty_series`.
- [ ] `roll_up_series_sizes`.
- [ ] `get_all_items`.

### 4.5 Move watch aggregation

Move to `client/watch.py`:

- [ ] `get_watchers_per_item`.
- [ ] `get_watch_counts_per_item`.
- [ ] Watch-specific filtering helpers.

### 4.6 Preserve the public façade

- [ ] Re-export every existing public client function from `client/__init__.py`.
- [ ] Add an explicit `__all__` documenting the supported surface.
- [ ] Update new internal imports to use the owning module directly.
- [ ] Leave existing external-style imports working.
- [ ] Avoid renaming functions during the move.

### Phase 4 acceptance criteria

- [ ] Existing imports from `jellyfin_utils.client` remain valid.
- [ ] `client/__init__.py` contains no implementation logic.
- [ ] Pagination, library mapping, and watch aggregation each have one owner.
- [ ] `tests/test_client.py` passes without behavior changes.
- [ ] The full validation gate passes.

---

## Phase 5 — Enforce architecture boundaries

**Objective:** prevent the same coupling and responsibility drift from
returning.

**Estimate:** 3–4 hours.

**Dependencies:** complete after Phases 2–4 settle their final module
boundaries.

### 5.1 Add architecture tests

Create `tests/test_architecture.py` using the standard-library `ast` module. Do
not add a dependency-analysis framework for these initial rules.

- [ ] Assert only `jellyfin_utils/cli.py` imports and registers feature command
      groups.
- [ ] Assert service/workflow modules do not import feature CLI modules.
- [ ] Assert render modules do not import `requests`, `jellyfin_utils.http`, or
      client transport APIs.
- [ ] Assert snapshot/model modules do not import Click or HTTP modules.
- [ ] Assert `analysis/cli.py` consumes watched/stale services rather than their
      CLI modules.
- [ ] Print the violating import and source path when a rule fails.

### 5.2 Document module ownership

Update the project-layout section in `README.md`:

- [ ] Describe `media/` as shared acquisition/rendering infrastructure.
- [ ] Describe user models, snapshot logic, repository, workflow, and rendering
      boundaries.
- [ ] Describe the client façade and its internal modules.
- [ ] Add the rule: CLI modules translate inputs and emit results; they do not
      own reusable business logic.
- [ ] Add the rule: render modules do not perform API I/O.

### 5.3 Remove temporary compatibility code

- [ ] Search for imports from `jellyfin_utils.user.service`.
- [ ] Keep re-exports that are plausibly external API; document them.
- [ ] Remove only re-exports proven unused and intentionally private.
- [ ] Search for imports from the old client implementation locations.
- [ ] Run architecture tests after each removal.

### Phase 5 acceptance criteria

- [ ] Architecture tests fail with a clear message when a forbidden import is
      introduced.
- [ ] README architecture documentation matches the actual package tree.
- [ ] No temporary module contains unexplained dead compatibility code.
- [ ] The full validation gate passes.

---

## Deferred work

Do not combine these changes with the five phases above.

- [ ] Revisit a stateful Jellyfin client only if connection configuration
      continues spreading.
- [ ] Revisit `server/cli.py` and `analysis/cli.py` only when new commands make
      their current size harder to manage.
- [ ] Address URL allowlisting or SSRF policy as a separate security decision;
      configurable server URLs are currently intentional CLI behavior.
- [ ] Define output-path restrictions only if the CLI gains a trusted
      working-directory policy.
- [ ] Consider raising the coverage threshold after the refactors add stable
      tests.

## Final completion checklist

- [ ] All five phases meet their acceptance criteria.
- [ ] `uv run pytest` passes with the enforced coverage threshold.
- [ ] Ruff formatting and linting pass.
- [ ] ty passes without new ignores.
- [ ] prek passes on all files.
- [ ] CI passes on Python 3.12, 3.13, and 3.14.
- [ ] CLI help, command names, options, exit codes, and output contracts remain
      compatible.
- [ ] README architecture documentation is current.
