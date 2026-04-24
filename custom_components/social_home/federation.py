"""Federation base URL bridge.

The Social Home server by itself cannot tell whether it is reachable
from the outside world — it only sees the HA Supervisor's Ingress
URL, which is private. The HA integration, running *inside* Home
Assistant, does know: it has access to :func:`get_url`, which
prefers a Nabu Casa Remote UI URL, then the admin-set
``external_url``. Pushing that URL to the server lets Social Home:

* stamp the current URL into every new pairing QR so invitees can
  reach this instance directly;
* notify already-paired peers via ``URL_UPDATED`` when the URL
  changes (e.g. the user enables Remote UI for the first time, or
  their DuckDNS hostname moves) so their ``remote_inbox_url``
  tracks the move without manual re-pairing.

This module is pure integration glue. It never creates entities and
never blocks ``async_setup_entry`` — federation failures are
logged and shrugged off so a transient network blip doesn't keep
HA from finishing setup.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.const import EVENT_CORE_CONFIG_UPDATE
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.network import NoURLAvailableError, get_url
from socialhome_client import SHClientError, SocialHomeClient

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)


def _resolve_external_url(hass: HomeAssistant) -> str | None:
    """Return the best externally-reachable URL for this HA instance.

    Preference order: Nabu Casa Remote UI → admin-set
    ``external_url`` → nothing. Internal URLs are excluded on
    purpose — pushing ``http://homeassistant.local:8123`` as the
    federation base would be worse than pushing nothing, since
    peers would confidently dial an address that only resolves on
    the owner's LAN.
    """
    try:
        return get_url(
            hass,
            allow_internal=False,
            allow_external=True,
            allow_cloud=True,
            prefer_external=True,
            require_ssl=False,
        )
    except NoURLAvailableError:
        return None


async def async_push_federation_base(hass: HomeAssistant, client: SocialHomeClient) -> None:
    """Push the current external URL to the Social Home server.

    No-op when HA has no externally-reachable URL configured. The
    server-side endpoint is idempotent — pushing an unchanged
    value returns ``changed=False`` and fans out nothing.
    """
    url = _resolve_external_url(hass)
    if url is None:
        _LOGGER.debug("Social Home: no external URL configured in HA; skipping federation push")
        return
    try:
        result = await client.federation.set_base(url)
    except SHClientError as err:
        # Federation binding is best-effort. A transient 5xx or
        # connection reset here must not block setup; the next
        # ``core_config_updated`` event (or the next HA restart)
        # will retry.
        _LOGGER.warning("Social Home: federation base push failed: %s", err)
        return
    if result.changed:
        _LOGGER.info(
            "Social Home: federation base set to %s (%d peer(s) notified)",
            result.base,
            result.peers_notified,
        )


def async_register_federation_listener(
    hass: HomeAssistant, entry: ConfigEntry, client: SocialHomeClient
) -> None:
    """Re-push the external URL whenever HA's core config changes.

    ``core_config_updated`` fires when the user edits
    ``external_url``, toggles Nabu Casa, or otherwise mutates
    :attr:`hass.config`. We don't need to filter — the federation
    endpoint is idempotent and ignores unchanged values cheaply.
    """

    async def _on_config_update(_event: Event) -> None:
        await async_push_federation_base(hass, client)

    entry.async_on_unload(hass.bus.async_listen(EVENT_CORE_CONFIG_UPDATE, _on_config_update))
