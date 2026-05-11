"""Social Home HA custom integration — setup + unload.

Spec §7.10. No entity platforms yet (see :data:`PLATFORMS` —
empty list); this module owns the per-entry shared client plus
the always-on bridges that don't surface as entities:

* federation base URL push (spec §7.10) — the HA integration is
  the only party that knows the instance's externally-reachable
  URL, so we push it to the server on setup and whenever
  ``core_config_updated`` fires.
* STUN/TURN ICE-server push (spec §7.10) — HA aggregates the
  operator's STUN/TURN config (YAML + Nabu Casa Cloud + other
  registered providers) via ``web_rtc.async_get_ice_servers``;
  we forward it to the server so SH's WebRTC peers use the same
  relays HA itself would use for cameras.
* person location push (spec §7.3) — ``state_changed`` listener
  that forwards ``person.*`` updates to
  ``POST /api/presence/location``. Gated on the ``sync_location``
  option so users can opt out without removing the integration.
* federation inbox relay (spec §7.10) — public HA HTTP view at
  ``/api/socialhome/inbox/{inbox_id}`` that proxies raw envelopes
  from remote Social Home instances into the add-on's internal
  ``/federation/inbox/{inbox_id}``.

Entity platforms (sensor / calendar / notify / shopping / …) land
one at a time in follow-up work; the polling coordinator + WS
manager will come back with the first platform that actually
needs them.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from socialhome_client import SHAuthError, SHClientError, SocialHomeClient

from .const import CONF_TOKEN, CONF_URL, DEFAULT_SYNC_LOCATION, OPT_SYNC_LOCATION, PLATFORMS
from .federation import (
    async_push_federation_base,
    async_register_federation_listener,
)
from .federation_inbox import async_register_inbox_view
from .ice_servers import (
    async_push_ice_servers,
    async_register_ice_servers_listener,
)
from .presence import async_setup_presence


@dataclass(slots=True)
class SocialHomeRuntimeData:
    """Per-entry shared client. Stored on
    :attr:`ConfigEntry.runtime_data` (HA ≥ 2024.10) and torn down
    by :func:`async_unload_entry`."""

    client: SocialHomeClient


type SocialHomeConfigEntry = ConfigEntry[SocialHomeRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: SocialHomeConfigEntry) -> bool:
    """Initialise one Social Home config entry.

    Builds the shared client, round-trips ``GET /api/me`` once as a
    setup-time canary (so HA surfaces a bad token or unreachable
    server up front instead of silently failing the first bridge
    call), and stashes the client on ``entry.runtime_data``.
    """
    client = SocialHomeClient(entry.data[CONF_URL], entry.data[CONF_TOKEN])

    # Setup canary: same call the config flow already proved works
    # on add-integration. Mapping errors to the HA-standard
    # exceptions gets us the re-auth flow (``SHAuthError`` → 401)
    # and the automatic retry (``SHClientError`` → transport) for
    # free. Close the session on any failure so a retry gets a
    # clean one.
    try:
        await client.me.get()
    except SHAuthError as err:
        await client.close()
        raise ConfigEntryAuthFailed("Social Home token rejected") from err
    except SHClientError as err:
        await client.close()
        raise ConfigEntryNotReady(f"Social Home unreachable: {err}") from err

    entry.runtime_data = SocialHomeRuntimeData(client=client)

    # Federation base URL push — best-effort, never blocks setup.
    # The helper swallows its own ``SHClientError``s; the listener
    # handles later HA config changes.
    await async_push_federation_base(hass, client)
    async_register_federation_listener(hass, entry, client)

    # STUN/TURN ICE-server push — same best-effort shape. The
    # ``web_rtc`` integration is a hard dependency (see
    # ``manifest.json``), so ``async_get_ice_servers`` always
    # resolves; the listener re-pushes on ``core_config_updated``.
    await async_push_ice_servers(hass, client)
    async_register_ice_servers_listener(hass, entry, client)

    # Public inbox URL for inbound federation envelopes. The view
    # is stateless per request, looks up the live config entry on
    # every call, and is idempotent across reloads — one
    # registration per HA process.
    async_register_inbox_view(hass)

    # Location forwarder — only attach when the user wants HA →
    # Social Home presence sync. Toggling the option triggers a
    # full reload, so re-reading on setup is enough.
    if entry.options.get(OPT_SYNC_LOCATION, DEFAULT_SYNC_LOCATION):
        async_setup_presence(hass, entry, client)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_options_change))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SocialHomeConfigEntry) -> bool:
    """Tear down a config entry's runtime objects.

    Forwards unload to every platform first, then closes the shared
    HTTP session. Returns ``False`` only if a platform refuses to
    unload — in which case HA keeps the entry active.
    """
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.client.close()
    return unloaded


async def _async_reload_on_options_change(
    hass: HomeAssistant, entry: SocialHomeConfigEntry
) -> None:
    """Reload the entry when the options flow saves new options.

    Cheaper than wiring each platform to re-read options on the fly
    — full reload is a few hundred milliseconds and keeps the
    per-platform logic simple.
    """
    await hass.config_entries.async_reload(entry.entry_id)
