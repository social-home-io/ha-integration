# AGENTS.md — ha-integration

AI agent instruction file. Read before editing. Canonical spec:
`spec_work.md` §7 in the Social Home meta-repo.

### Architecture rules
- Python 3.14 floor — HA Core's own floor is 3.14.2 (2026.3+).
- Never import from `social_home` (core). Runtime deps: `homeassistant`
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
- Changed the config-flow UI strings? Update both `strings.json` and
  `translations/en.json` in the same commit.
- Changed the coordinator's polled endpoint or interval? Update the
  `SocialHomeCoordinator` docstring and the spec reference.

### File locations
- Integration code: `custom_components/social_home/`
- Tests: `tests/` (mirrors the module tree)
