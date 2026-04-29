# Design principles

Why this integration stays thin. Distilled from §7 of `spec_work.md`
plus the existing `CLAUDE.md` / `AGENTS.md` rules.

## A thin bridge, not a second app

The heavy lifting — HTTP, WebSocket, models, retries — lives in
`socialhome-client`. This repo is HA glue: config flow, coordinator,
entity platforms, and the few bridges that translate HA primitives
(zones, calendars, push) into Social Home API calls. No business
logic; no protocol parsing; no state beyond what HA itself owns.

## Python 3.14 floor

Home Assistant Core raised its floor to 3.14.2 in the 2026.3
release. `homeassistant` and `pytest-homeassistant-custom-component`
no longer resolve on 3.13, so this integration follows. `from
__future__ import annotations` still goes in every module.

## CalVer releases without a `v` prefix

Tags look like `2026.4.25`; `manifest.json`'s `version` field must
match the tag. The release workflow enforces this. SemVer tags will
fail HACS *and* the release publish — match the convention the rest
of the social-home project uses (`socialhome`, `socialhome-client`,
`ha-app`).

## Never import from core

The only runtime deps beyond Home Assistant are `socialhome-client`
(declared in `manifest.json`'s `requirements`) and Python stdlib.
The Social Home core (`social_home` package) lives in a separate
repo, runs Python 3.14, and would drag a large dependency graph into
HA Core if imported. The integration crosses the boundary only over
HTTP + WS via `socialhome-client`.

## All I/O is async

Home Assistant forbids synchronous I/O on the event loop. Every call
into `socialhome-client` is awaited; every HA service call uses
`hass.services.async_call(..., blocking=True)`; no `time.sleep`.

## Imports stay at the top

Every module's imports live at the top. The only exception is
`if TYPE_CHECKING:` blocks for type-only circular dependencies. No
inline imports inside coordinator update methods, no lazy loaders.

## `ConfigEntry.runtime_data` is the only shared state

The integration's runtime state — `SocialHomeClient` and
`SocialHomeCoordinator` — lives on `entry.runtime_data`.
`async_setup_entry` builds them; `async_unload_entry` tears them
down. No module-level singletons, no `hass.data[DOMAIN]` dicts.

## Auth failure goes to re-auth, transient failure to update-failed

The coordinator maps client exceptions onto HA's contract:

| Client raises | Coordinator raises |
|---|---|
| `SHAuthError` (401) | `ConfigEntryAuthFailed` — HA opens the re-auth flow |
| `SHClientError` (any other non-2xx) | `UpdateFailed` — HA shows a temporary error |
| `SHNotFoundError` | bubbles up to specific call sites; not coordinator-wide |

Arbitrary exceptions never escape coordinator updates. Every code
path that could touch the network catches the typed exception
hierarchy from `socialhome-client`.

## The bearer token is private

The user's API token lives in `ConfigEntry.data` and travels as
`Authorization: Bearer …`. It never appears in logs, attributes,
events, or error messages. HA's diagnostics download path redacts
it via the `to_redact` list when the integration ships a diagnostics
export.

## GPS gates apply on the integration side too

Presence updates land here from `state_changed` of `person.*`
entities. Before forwarding, the bridge applies three gates that
mirror the §4 / §7.3 invariants:

1. **Accuracy cap:** drop coordinates with `gps_accuracy > 500 m`
   (still push the zone so automations keep working).
2. **Distance dedup:** skip if the position moved less than 50 m
   from the last forwarded update (haversine).
3. **4dp truncation:** `round(float(lat), 4)` before the API call —
   hard cap on precision before the wire, independent of what HA
   reported.

These gates protect the user even if the upstream Social Home server
is misconfigured — defence in depth.

## Test boundary, not production boundary

Tests stub HA plumbing via `pytest-homeassistant-custom-component`
and `socialhome-client` via fixtures that patch both import sites.
Production code never carries env-var-gated stubs or test-only
branches.

## Spec references

- §7 — repository overview
- §7.1 — manifest, requirements, integration type
- §7.3 — presence bridge (the GPS gates)
- §7.10 — federation base URL & integration requirements
- §7.11 — lifecycle (setup, unload, cleanup)
- §7.12 — federation inbox bridge
