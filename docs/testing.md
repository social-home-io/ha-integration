# Test strategy

How tests are organised, what coverage is gated, and how the HA
plumbing is faked. Distilled from §7 / §27 of `spec_work.md` plus the
existing `tests/` tree.

## Principles

- **Branch coverage gate: 85 %.** Configured in CI; matches
  `socialhome-client`. The integration is mostly thin glue, so
  per-method branches are shallow.
- **`pytest` only; no `unittest.TestCase`.** Async tests use
  `pytest-asyncio` with `asyncio_mode = "auto"`, so
  `@pytest.mark.asyncio` is implicit.
- **Plain `async def test_xxx()` functions, no `TestXxx` classes.**
  One test file per module, mirroring the source tree.
- **HA plumbing is faked, not imported.** `homeassistant` and
  `pytest-homeassistant-custom-component` are heavy — most tests
  use lightweight stubs from `conftest.py`. The end-to-end
  config-flow run uses the real HA fixtures.
- **`socialhome-client` is mocked at the test boundary.** Production
  code never carries env-var-gated stubs.

## Layout

```
tests/
├── conftest.py                shared fixtures: mock_client, config_entry,
│                              sample_user
├── test_config_flow.py        user / Hassio / reauth / options
├── test_federation.py         base URL push + listener + config updates
├── test_federation_inbox.py   POST handling + error codes + multi-account
├── test_init.py               entry setup / unload / platform forwarding
└── test_presence.py           state_changed → API + four GPS gates
```

The tree mirrors `custom_components/socialhome/`. A new module
needs its matching `tests/test_<module>.py`; new behaviour on an
existing module gets at least one new `async def test_xxx()` in the
matching file.

## Shared fixtures (`conftest.py`)

| Fixture | What it provides |
|---|---|
| `config_entry` | A fake `ConfigEntry` with realistic data + entry_id |
| `sample_user` | A `User` dataclass with sensible defaults |
| `mock_client` | An `AsyncMock` patched into both import sites — the test exercises code paths without ever talking to a real HFS |

`mock_client` patches the import name in **both** `__init__.py` and
`config_flow.py` — they import `SocialHomeClient` separately, and
patching only one leaves a real client wired into the other.

## Patterns

### Setup-canary error mapping

`async_setup_entry` runs a single `client.me.get()` so a bad token
or unreachable server fails setup loudly. `test_init.py` covers
both branches by sideways-injecting the failure on the mocked
client:

```python
async def test_setup_entry_auth_failure_triggers_reauth(
    hass, config_entry, mock_client,
):
    mock_client.return_value.me.get = AsyncMock(side_effect=SHAuthError())
    config_entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
```

Same shape exercises `SHClientError → SETUP_RETRY`.

### Presence gates

`test_presence.py` is the longest file — each gate has its own
test:

- accuracy > 500 m → no API call
- moved < 50 m → no API call
- 4dp truncation in the request body (`round(float(lat), 4)` matches
  the §4 invariant)
- `state="home"` / `state="<zone>"` → zone path, no coordinates

Each test fires a synthetic `state_changed` event through
`hass.bus.async_fire("state_changed", ...)` and asserts on the
`mock_client.presence.post_location` call shape.

### Federation inbox

`test_federation_inbox.py` POSTs raw bytes to the registered view
and asserts the upstream `forward_inbox_envelope()` call happened
with the same bytes, status code, and content-type. Multi-account
fallback (two entries → route to the right runtime client) has its
own test.

## Running locally

```sh
pip install -e .[dev]
pytest                                       # full suite, gated at 85 %
pytest -k presence                           # one module
ruff check custom_components/ tests/
mypy custom_components/socialhome/
```

`pre-commit install` runs the same set on every commit.

## Releasing

CalVer tag (no `v` prefix) triggers the GitHub Actions release
workflow:

```sh
git tag 2026.4.25
git push origin 2026.4.25
```

The workflow asserts `manifest.json`'s `version` matches the tag and
publishes the release to GitHub. HACS picks up the release; users
update from the *Settings → Devices & services → Updates* tile.

## Spec references

- §7 — repository overview
- §7.2 — config flow (covered by `test_config_flow.py`)
- §7.3 — presence bridge with the four GPS gates
- §7.10 — federation base URL push + integration requirements
- §7.11 — setup / unload lifecycle
- §7.12 — federation inbox bridge
- §27 — core's test strategy (this integration follows the same
  principles with a lower coverage gate)
