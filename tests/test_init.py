"""Tests for ``custom_components.social_home.__init__``.

Covers ``async_setup_entry`` success + failure paths and
``async_unload_entry`` cleanup.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import HomeAssistant, State
from pytest_homeassistant_custom_component.common import MockConfigEntry
from socialhome_client import SHAuthError, SHClientError

from custom_components.social_home import SocialHomeRuntimeData
from custom_components.social_home.const import CONF_TOKEN, CONF_URL


async def test_setup_entry_success(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_client: MagicMock,
    mock_ws_manager: MagicMock,
) -> None:
    """Happy path: client + coordinator built, entry LOADED, no platforms."""
    config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    mock_client.assert_called_once_with("http://sh.test", "token-abc")
    runtime = config_entry.runtime_data
    assert isinstance(runtime, SocialHomeRuntimeData)
    # First refresh was issued — exactly one unread_summary round-trip.
    assert runtime.client.me.unread_summary.await_count == 1


async def test_setup_entry_auth_failure_triggers_reauth(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_client: MagicMock,
    mock_ws_manager: MagicMock,
) -> None:
    """A 401 on first refresh maps to ``SETUP_ERROR`` + re-auth flow."""
    mock_client.return_value.me.unread_summary = AsyncMock(side_effect=SHAuthError())
    config_entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    # Client was closed so we don't leak a session.
    mock_client.return_value.close.assert_awaited()
    # HA should now be running a re-auth flow.
    flows = hass.config_entries.flow.async_progress_by_handler("social_home")
    assert any(f["context"].get("source") == "reauth" for f in flows)


async def test_setup_entry_transport_error_is_retry(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_client: MagicMock,
    mock_ws_manager: MagicMock,
) -> None:
    """Transport errors map to ``SETUP_RETRY`` (HA will retry later)."""
    mock_client.return_value.me.unread_summary = AsyncMock(side_effect=SHClientError("boom"))
    config_entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    mock_client.return_value.close.assert_awaited()


async def test_unload_entry_closes_client_and_ws(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_client: MagicMock,
    mock_ws_manager: MagicMock,
) -> None:
    """Unload disconnects WS and closes the HTTP session."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.NOT_LOADED
    mock_ws_manager.return_value.disconnect.assert_awaited_once()
    mock_client.return_value.close.assert_awaited()


async def test_options_update_reloads_entry(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_client: MagicMock,
    mock_ws_manager: MagicMock,
) -> None:
    """Saving new options reloads the entry so platforms re-read them."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    first_runtime = config_entry.runtime_data

    hass.config_entries.async_update_entry(config_entry, options={"sync_location": False})
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    # Reload rebuilds runtime_data — different object.
    assert config_entry.runtime_data is not first_runtime


async def test_config_entry_url_and_token_wired(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_client: MagicMock,
    mock_ws_manager: MagicMock,
) -> None:
    """Regression: URL + token stored on the entry flow through to the client."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    mock_client.assert_called_once_with(config_entry.data[CONF_URL], config_entry.data[CONF_TOKEN])


async def test_setup_attaches_presence_listener_when_option_on(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_client: MagicMock,
    mock_ws_manager: MagicMock,
) -> None:
    """Default options include ``sync_location=True`` → presence listener fires."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    hass.bus.async_fire(
        EVENT_STATE_CHANGED,
        {
            "entity_id": "person.pascal",
            "new_state": State("person.pascal", "home", {"latitude": 1.0, "longitude": 2.0}),
            "old_state": None,
        },
    )
    await hass.async_block_till_done()

    mock_client.return_value.presence.post_location.assert_awaited_once()


async def test_setup_skips_presence_listener_when_option_off(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_client: MagicMock,
    mock_ws_manager: MagicMock,
) -> None:
    """``sync_location=False`` → no presence listener, no push fires."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, options={"sync_location": False})
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    hass.bus.async_fire(
        EVENT_STATE_CHANGED,
        {
            "entity_id": "person.pascal",
            "new_state": State("person.pascal", "home", {"latitude": 1.0, "longitude": 2.0}),
            "old_state": None,
        },
    )
    await hass.async_block_till_done()

    mock_client.return_value.presence.post_location.assert_not_awaited()
