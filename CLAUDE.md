# CLAUDE.md — ha-integration

Instruction file for Claude Code. Read before editing.

## What this is

Home Assistant custom integration for Social Home. Distributed via
HACS under the domain `social_home`. Spec: §7 of `spec_work.md` in
the Social Home meta-repo.

The integration is a thin bridge — the heavy lifting lives in
`socialhome-client` (HTTP + WS). Keep this repo focused on HA glue:
config flow, coordinator, entity platforms.

## Hard rules

- **Python 3.14 floor.** HA Core raised its floor to 3.14.2 in
  2026.3; ``homeassistant`` and
  ``pytest-homeassistant-custom-component`` no longer resolve on
  3.13. ``from __future__ import annotations`` still goes in every
  module.
- **CalVer releases (no ``v`` prefix).** Tags look like
  ``2026.4.25``; ``manifest.json``'s ``version`` field must match
  the release tag (``release.yml`` enforces this on every
  publish). Match the convention the rest of the social-home
  project uses (``socialhome``, ``socialhome-client``, ``ha-app``,
  …) — semver tags will fail HACS + the release workflow.
- **Never import from `social_home` (core).** The only runtime
  dependency beyond Home Assistant is `socialhome-client>=1.0.0`
  (declared in `manifest.json`).
- **All I/O is async.** No `time.sleep`, no blocking calls. HA
  forbids sync I/O in the event loop.
- **All imports at the top of the file.** Only `if TYPE_CHECKING:`
  exceptions; no inline imports inside functions.
- **Tests are plain `async def test_xxx()` functions** — no
  `TestXxx` classes. One test file per module, mirroring the tree.
- **Never expose the user API token.** It lives in
  `ConfigEntry.data` and travels as `Authorization: Bearer …`.
  Don't log it; don't surface it in events, attributes, or error
  messages.
- **`ConfigEntry.runtime_data` is the only shared state.** Put the
  `SocialHomeClient` and `SocialHomeCoordinator` there; unload
  tears both down cleanly.
- **Raise `ConfigEntryAuthFailed` on 401 (`SHAuthError`).** HA then
  routes the user through the re-auth flow.
- **Raise `UpdateFailed` on transient errors (`SHClientError`).**
  Never let arbitrary exceptions escape coordinator updates.

## Layout

```
custom_components/social_home/
  manifest.json         # HACS manifest — domain, version, requirements
  __init__.py           # async_setup_entry / async_unload_entry
  const.py              # DOMAIN, platform list, option keys, defaults
  coordinator.py        # SocialHomeCoordinator (polls unread-summary)
  config_flow.py        # user + hassio + reauth + options flows
  strings.json          # HA UI strings (source of truth)
  translations/en.json  # en mirror of strings.json
tests/                  # pytest tree mirroring the module tree
```

Entity platforms (`sensor.py`, `calendar.py`, `notify.py`,
`shopping_list.py`, `presence.py`) are intentionally absent in the
initial skeleton — the spec drops them in per-platform.

## Testing

```sh
pip install -e .[dev]
pytest                      # ≥85 % branch coverage gate
ruff check custom_components/ tests/
mypy custom_components/social_home/
```

Tests use stub fakes for the HA `HomeAssistant` / `ConfigEntry`
plumbing — we don't depend on `homeassistant` as a test-time
import because its full dependency tree is large. When a real HA
fixture is needed (e.g. full end-to-end config-flow run), use the
`pytest-homeassistant-custom-component` plugin.

## Keep docs in sync

`docs/` is the public reference for the integration's design. Treat
the matching doc file as part of the same change:

- **Added a new entity platform** (`sensor.py`, `calendar.py`,
  `notify.py`, `shopping_list.py`)? Add an entry to the lifecycle
  diagram and the "Where things live" table in
  `docs/architecture.md`. Drop the platform name into `PLATFORMS`
  in `const.py` AND mention it in `docs/architecture.md`.
- **Changed the coordinator** (different polled endpoint, new
  interval, additional exception mapping)? Update the coordinator
  section + exception-mapping table in `docs/architecture.md`.
- **Changed the config flow or its options** (new flow, new option
  toggle, validation tweak)? Update both the strings/translations
  files AND the config-flow table in `docs/architecture.md`.
- **Changed the presence bridge gates** (accuracy cap, distance
  dedup threshold, GPS precision)? Update the four-gate list in
  `docs/principles.md` AND the matching section in
  `docs/architecture.md`.
- **Changed the test strategy** (coverage gate, mock approach,
  shared fixtures)? Update `docs/testing.md` so the fixtures table
  + commands match `tests/conftest.py` and CI.
- **Touched a §7 invariant** (raised the Python floor, added a
  runtime dependency, started importing from core, exposed the
  bearer token)? Update `docs/principles.md` AND flag the change
  in the PR description for explicit reviewer sign-off.
- **Added a new top-level doc file under `docs/`?** Link it from
  `docs/README.md` and from the repo-root `README.md`.

Reviewer checklist: if a PR adds a platform, changes the
coordinator, changes the config flow, changes a presence gate, or
shifts the test strategy and the docs aren't touched, push back.
Incremental accuracy beats big-bang rewrites.
