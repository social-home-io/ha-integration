"""STUN/TURN ICE-server push to the Social Home backend.

The Social Home server runs *behind* HA (Ingress or reverse proxy),
which means it can't discover the operator's STUN/TURN setup on its
own. HA, on the other hand, already aggregates ICE servers from
three sources:

* ``hass.config.webrtc.ice_servers`` — populated from the
  ``homeassistant:`` block in ``configuration.yaml``;
* the user-YAML registered against the ``web_rtc`` integration;
* runtime providers (Nabu Casa Cloud registers a STUN+TURN pair
  here once the user opts in via cloud preferences).

The public entry point ``homeassistant.components.web_rtc.async_get_ice_servers``
returns the union of those sources, with HA's default
``stun.home-assistant.io`` pair only when nothing else is
configured. We forward that list to ``PUT /api/ha/integration/ice-servers``
so the SH WebRTC stack uses the same servers HA itself would use
for camera streams.

The push is best-effort: a transient ``SHClientError`` is logged
at WARN and never re-raised, mirroring ``federation.py``. The
listener re-pushes on ``EVENT_CORE_CONFIG_UPDATE`` (which fires on
YAML reload and on ``hass.config.async_update``) so changes
propagate without an HA restart.

Note: HA itself does not emit a dedicated "ICE servers changed"
bus event when a runtime provider (e.g. Nabu Casa Cloud) registers
or unregisters. Cloud-driven changes are still picked up on the
next ``core_config_updated`` fire, on the next entry reload, or on
the next HA restart — the worst case is a few minutes of stale
state on the SH side, which the SH backend treats as harmless
(unchanged-list pushes are no-ops).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.web_rtc import async_get_ice_servers
from homeassistant.const import EVENT_CORE_CONFIG_UPDATE
from homeassistant.core import Event, HomeAssistant
from socialhome_client import SHClientError, SocialHomeClient

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)


def _resolve_ice_servers(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Return HA's current ICE-server list in Chrome ``RTCIceServer`` shape.

    Delegates to ``web_rtc.async_get_ice_servers`` and serialises each
    entry via ``RTCIceServer.to_dict()`` so the wire payload matches
    what HA's own front-end sees over ``web_rtc/ice_servers``. The
    socialhome-client accepts plain dicts in this shape directly.

    When HA has neither YAML nor a registered provider, ``web_rtc``
    falls back to its default ``stun.home-assistant.io`` pair. We
    forward that on purpose — pushing nothing would leave the SH
    backend with its own (possibly stale) hard-coded defaults.
    """
    return [server.to_dict() for server in async_get_ice_servers(hass)]


async def async_push_ice_servers(hass: HomeAssistant, client: SocialHomeClient) -> None:
    """Push the current HA ICE-server list to the Social Home server.

    No-op when HA returns an empty list (shouldn't happen with the
    ``web_rtc`` defaults in place, but the server-side endpoint
    rejects empty pushes — best to skip rather than 4xx). Server
    is idempotent: pushing an unchanged list returns
    ``changed=False`` and fans out nothing.
    """
    servers = _resolve_ice_servers(hass)
    if not servers:
        _LOGGER.debug("Social Home: no ICE servers resolved from HA; skipping push")
        return
    try:
        result = await client.ha.set_ice_servers(servers)
    except SHClientError as err:
        # ICE-server binding is best-effort. A transient 5xx or
        # connection reset here must not block setup; the next
        # ``core_config_updated`` event (or the next HA restart)
        # will retry.
        _LOGGER.warning("Social Home: ICE servers push failed: %s", err)
        return
    if result.changed:
        _LOGGER.info(
            "Social Home: ICE servers updated (%d entry(ies))",
            len(result.ice_servers),
        )


def async_register_ice_servers_listener(
    hass: HomeAssistant, entry: ConfigEntry[Any], client: SocialHomeClient
) -> None:
    """Re-push the ICE-server list whenever HA's core config changes.

    ``core_config_updated`` is the broadest signal we have for
    operator-driven config changes — it covers YAML reloads and
    ``hass.config.async_update`` mutations. The endpoint is
    idempotent and ignores unchanged values cheaply, so an
    over-eager refire is harmless.
    """

    async def _on_config_update(_event: Event) -> None:
        await async_push_ice_servers(hass, client)

    entry.async_on_unload(hass.bus.async_listen(EVENT_CORE_CONFIG_UPDATE, _on_config_update))
