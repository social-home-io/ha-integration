"""Tests for ``custom_components.social_home.coordinator``.

The coordinator is a thin mapper between :class:`SocialHomeClient`
errors and Home Assistant's update-coordinator exception contract.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from socialhome_client import SHAuthError, SHClientError, UnreadSummary

from custom_components.social_home.coordinator import SocialHomeCoordinator


def _make_client(unread: AsyncMock) -> MagicMock:
    c = MagicMock()
    c.me = MagicMock()
    c.me.unread_summary = unread
    return c


async def test_update_returns_summary(
    hass: HomeAssistant, sample_unread: UnreadSummary, mock_ws_manager: MagicMock
) -> None:
    """Success path returns the parsed :class:`UnreadSummary`."""
    client = _make_client(AsyncMock(return_value=sample_unread))
    coord = SocialHomeCoordinator(hass, client)

    result = await coord._async_update_data()

    assert result is sample_unread
    client.me.unread_summary.assert_awaited_once_with()
    mock_ws_manager.assert_called_once_with(client)


async def test_update_maps_auth_error(hass: HomeAssistant, mock_ws_manager: MagicMock) -> None:
    """A 401 is surfaced as :class:`ConfigEntryAuthFailed`."""
    client = _make_client(AsyncMock(side_effect=SHAuthError()))
    coord = SocialHomeCoordinator(hass, client)

    with pytest.raises(ConfigEntryAuthFailed):
        await coord._async_update_data()


async def test_update_maps_client_error(hass: HomeAssistant, mock_ws_manager: MagicMock) -> None:
    """Any other client failure becomes :class:`UpdateFailed`."""
    client = _make_client(AsyncMock(side_effect=SHClientError("boom")))
    coord = SocialHomeCoordinator(hass, client)

    with pytest.raises(UpdateFailed):
        await coord._async_update_data()


async def test_update_interval_is_60_seconds(
    hass: HomeAssistant, mock_ws_manager: MagicMock
) -> None:
    """Matches spec §6.2a — 60-second polling cadence."""
    client = _make_client(AsyncMock())
    coord = SocialHomeCoordinator(hass, client)
    assert coord.update_interval is not None
    assert coord.update_interval.total_seconds() == 60.0
