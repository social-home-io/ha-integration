# AGENTS.md — ha-integration

AI agent instruction file. Read before editing. Canonical spec:
`spec_work.md` §7 in the Social Home meta-repo.

### Architecture rules
- Python 3.14 floor — HA Core's own floor is 3.14.2 (2026.3+).
- CalVer release tags (e.g. ``2026.4.25``); ``manifest.json``'s
  ``version`` must match the release tag.
- Never import from `socialhome` (core). Runtime deps: `homeassistant`
  and `socialhome-client>=1.0.0`.
- All I/O is async; no `time.sleep`, no blocking calls.
- All imports at the top of the file; only `if TYPE_CHECKING:`
  exceptions.
- `ConfigEntry.runtime_data` owns the shared `SocialHomeClient` +
  `SocialHomeCoordinator`. `async_unload_entry` must close both.
- Coordinator maps `SHAuthError` → `ConfigEntryAuthFailed`, any other
  `SHClientError` → `UpdateFailed`. No other exception types escape.
- Never log, expose, or surface the bearer token.

### Testing
- Plain `async def test_xxx()` functions; no `TestXxx` classes.
- One test file per module, matching the tree.
- Coverage gate: 85 % branch.

### Keep docs in sync
Docs live in `docs/`. Ship the matching doc update in the same
commit:
- New entity platform (`sensor.py`, `calendar.py`, `notify.py`,
  `shopping_list.py`) → update the lifecycle diagram + "Where
  things live" table in `docs/architecture.md`.
- Changed coordinator (different polled endpoint, new interval,
  additional exception mapping) → coordinator section + exception
  table in `docs/architecture.md`.
- Changed the config flow or its options → update both the
  strings/translations files AND the config-flow table in
  `docs/architecture.md`.
- Changed the presence bridge gates (accuracy cap, distance dedup,
  GPS precision) → four-gate list in `docs/principles.md` AND the
  matching section in `docs/architecture.md`.
- Test-strategy change (coverage gate, mock approach, shared
  fixtures) → `docs/testing.md`.
- §7 invariant touched (raise the Python floor, add a runtime
  dependency, import from core, expose the bearer token) →
  `docs/principles.md`, **and** flag in the PR description for
  explicit reviewer sign-off.
- New top-level doc file under `docs/` → link from `docs/README.md`
  and from the repo-root `README.md`.

### File locations
- Integration code: `custom_components/socialhome/`
- Tests: `tests/` (mirrors the module tree)
- Docs: `docs/` (principles, architecture, testing)
