"""Shared :class:`DataUpdateCoordinator` for one Social Home config entry.

Spec §6.2a. Every platform the integration forwards to registers
its own entities against this coordinator so one HTTP poll feeds
them all, and the WS manager is available to platforms that want
sub-poll-interval updates.

Error handling follows the HA convention: 401 maps to
:class:`ConfigEntryAuthFailed` (triggers the re-auth flow), and
any other transport / protocol error maps to :class:`UpdateFailed`
so HA shows the entity as ``unavailable`` without tearing the
entry down.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from socialhome_client import (
    SHAuthError,
    SHClientError,
    SocialHomeClient,
    SocialHomeWsManager,
    UnreadSummary,
)

from .const import DOMAIN, UPDATE_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)


class SocialHomeCoordinator(DataUpdateCoordinator[UnreadSummary]):
    """Poll unread-summary and own the shared WS manager.

    One instance per config entry; stored on
    ``ConfigEntry.runtime_data`` so all platforms share it. The
    client is injected rather than created internally — the config
    flow already built one to validate credentials during setup.
    """

    def __init__(self, hass: HomeAssistant, client: SocialHomeClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )
        self.client = client
        self.ws_manager = SocialHomeWsManager(client)

    async def _async_update_data(self) -> UnreadSummary:
        """Fetch the current unread summary.

        Called by HA on the configured interval and whenever a
        platform calls :meth:`async_request_refresh`.
        """
        try:
            return await self.client.me.unread_summary()
        except SHAuthError as err:
            raise ConfigEntryAuthFailed("Social Home token rejected") from err
        except SHClientError as err:
            raise UpdateFailed(f"Social Home unreachable: {err}") from err
