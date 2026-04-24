"""Tests for ``custom_components.social_home.presence``.

Drive the listener directly by firing ``state_changed`` events on
the real HA bus, then inspect what the fake
``client.presence.post_location`` was awaited with. This keeps the
haversine / accuracy / truncation logic covered without reinventing
the SH client.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import HomeAssistant, State
from pytest_homeassistant_custom_component.common import MockConfigEntry
from socialhome_client import SHClientError

from custom_components.social_home.presence import async_setup_presence


def _client() -> MagicMock:
    c = MagicMock()
    c.presence = MagicMock()
    c.presence.post_location = AsyncMock()
    return c


def _fire_state(
    hass: HomeAssistant,
    entity_id: str,
    state: str,
    attrs: dict[str, float] | None = None,
) -> None:
    """Fire a ``state_changed`` with the given new_state.

    We build a real :class:`State` object so ``event.data['new_state']``
    has the same ``.attributes`` / ``.state`` surface the integration
    reads in production.
    """
    new_state = State(entity_id, state, attributes=attrs or {})
    hass.bus.async_fire(
        EVENT_STATE_CHANGED,
        {"entity_id": entity_id, "new_state": new_state, "old_state": None},
    )


async def test_ignores_non_person_entities(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """A ``sensor.*`` update is not a presence event — silently dropped."""
    client = _client()
    config_entry.add_to_hass(hass)
    async_setup_presence(hass, config_entry, client)

    _fire_state(hass, "sensor.kitchen", "on")
    await hass.async_block_till_done()

    client.presence.post_location.assert_not_awaited()


async def test_pushes_coords_zone_and_accuracy(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    client = _client()
    config_entry.add_to_hass(hass)
    async_setup_presence(hass, config_entry, client)

    _fire_state(
        hass,
        "person.pascal",
        "home",
        {"latitude": 52.5200, "longitude": 13.4050, "gps_accuracy": 12.0},
    )
    await hass.async_block_till_done()

    client.presence.post_location.assert_awaited_once_with(
        username="pascal",
        latitude=52.52,
        longitude=13.405,
        accuracy_m=12.0,
        zone_name="home",
    )


async def test_high_accuracy_drops_coordinates_keeps_zone(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Above the 500 m cap, latitude/longitude are nulled out."""
    client = _client()
    config_entry.add_to_hass(hass)
    async_setup_presence(hass, config_entry, client)

    _fire_state(
        hass,
        "person.pascal",
        "home",
        {"latitude": 52.52, "longitude": 13.405, "gps_accuracy": 2000.0},
    )
    await hass.async_block_till_done()

    client.presence.post_location.assert_awaited_once_with(
        username="pascal",
        latitude=None,
        longitude=None,
        accuracy_m=2000.0,
        zone_name="home",
    )


async def test_unknown_zone_state_becomes_none(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """``not_home`` / ``unknown`` map to ``zone_name=None`` on the wire."""
    client = _client()
    config_entry.add_to_hass(hass)
    async_setup_presence(hass, config_entry, client)

    _fire_state(hass, "person.pascal", "not_home", {"latitude": 1.0, "longitude": 2.0})
    await hass.async_block_till_done()

    client.presence.post_location.assert_awaited_once()
    kwargs = client.presence.post_location.await_args.kwargs
    assert kwargs["zone_name"] is None


async def test_dedups_small_movements(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    """Two updates within the 50 m dedup radius → one push."""
    client = _client()
    config_entry.add_to_hass(hass)
    async_setup_presence(hass, config_entry, client)

    _fire_state(
        hass,
        "person.pascal",
        "home",
        {"latitude": 52.5200, "longitude": 13.4050, "gps_accuracy": 10.0},
    )
    await hass.async_block_till_done()
    # ~11 m east — well inside the 50 m dedup radius.
    _fire_state(
        hass,
        "person.pascal",
        "home",
        {"latitude": 52.5200, "longitude": 13.40516, "gps_accuracy": 10.0},
    )
    await hass.async_block_till_done()

    assert client.presence.post_location.await_count == 1


async def test_emits_when_movement_exceeds_dedup(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    client = _client()
    config_entry.add_to_hass(hass)
    async_setup_presence(hass, config_entry, client)

    _fire_state(
        hass,
        "person.pascal",
        "home",
        {"latitude": 52.5200, "longitude": 13.4050, "gps_accuracy": 10.0},
    )
    await hass.async_block_till_done()
    # ~70 m east — outside the 50 m dedup radius, so a second push fires.
    _fire_state(
        hass,
        "person.pascal",
        "home",
        {"latitude": 52.5200, "longitude": 13.406, "gps_accuracy": 10.0},
    )
    await hass.async_block_till_done()

    assert client.presence.post_location.await_count == 2


async def test_truncates_to_four_decimal_places(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Coords sent on the wire are always 4 d.p. max, regardless of source."""
    client = _client()
    config_entry.add_to_hass(hass)
    async_setup_presence(hass, config_entry, client)

    _fire_state(
        hass,
        "person.pascal",
        "home",
        {"latitude": 52.5200123, "longitude": 13.4050987, "gps_accuracy": 5.0},
    )
    await hass.async_block_till_done()

    kwargs = client.presence.post_location.await_args.kwargs
    assert kwargs["latitude"] == 52.52
    assert kwargs["longitude"] == 13.4051


async def test_push_failure_is_swallowed(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """A failed push does not propagate up into the HA event bus."""
    client = _client()
    client.presence.post_location.side_effect = SHClientError("boom")
    config_entry.add_to_hass(hass)
    async_setup_presence(hass, config_entry, client)

    _fire_state(
        hass,
        "person.pascal",
        "home",
        {"latitude": 52.52, "longitude": 13.405, "gps_accuracy": 10.0},
    )
    # Must not raise even though the push threw.
    await hass.async_block_till_done()
    client.presence.post_location.assert_awaited_once()


async def test_missing_attributes_is_a_no_op_on_coords(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """A person with no GPS yet still emits zone-only (coords=None)."""
    client = _client()
    config_entry.add_to_hass(hass)
    async_setup_presence(hass, config_entry, client)

    _fire_state(hass, "person.pascal", "home", {})
    await hass.async_block_till_done()

    client.presence.post_location.assert_awaited_once_with(
        username="pascal",
        latitude=None,
        longitude=None,
        accuracy_m=None,
        zone_name="home",
    )
