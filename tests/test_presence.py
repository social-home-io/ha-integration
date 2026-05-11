"""Tests for ``custom_components.socialhome.presence``.

Drive the listener directly by firing ``state_changed`` events on
the real HA bus, then inspect what the fake
``client.presence.post_location`` was awaited with. This keeps the
haversine / accuracy / truncation logic covered without reinventing
the SH client.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import HomeAssistant, State
from pytest_homeassistant_custom_component.common import MockConfigEntry
from socialhome_client import SHClientError

from custom_components.socialhome.presence import async_setup_presence

#: Stand-in HA user_id for the linked person. Tests fire
#: ``state_changed`` carrying this id, and the per-test
#: ``async_get_user`` mock returns a User whose
#: homeassistant-provider credential maps it to a username.
_USER_ID = "user-abc"


def _client() -> MagicMock:
    c = MagicMock()
    c.presence = MagicMock()
    c.presence.post_location = AsyncMock()
    return c


def _seed_user(
    hass: HomeAssistant,
    *,
    user_id: str = _USER_ID,
    ha_username: str | None = "pascal",
) -> None:
    """Make ``hass.auth.async_get_user(user_id)`` return a stub User
    whose ``homeassistant``-provider credential carries
    ``ha_username``.

    Pass ``ha_username=None`` to simulate a user that exists but has
    no ``homeassistant``-provider credential (e.g. LDAP-only).
    """
    creds: list[Any] = []
    if ha_username is not None:
        cred = MagicMock()
        cred.auth_provider_type = "homeassistant"
        cred.data = {"username": ha_username}
        creds.append(cred)
    user = MagicMock()
    user.credentials = creds
    hass.auth.async_get_user = AsyncMock(return_value=user)


def _fire_state(
    hass: HomeAssistant,
    entity_id: str,
    state: str,
    attrs: dict[str, Any] | None = None,
    *,
    user_id: str | None = _USER_ID,
) -> None:
    """Fire a ``state_changed`` with the given new_state.

    ``user_id`` is merged into ``attrs`` by default — production
    person entities backed by an HA user always carry it. Pass
    ``user_id=None`` to simulate a manually-tracked person.
    """
    merged: dict[str, Any] = dict(attrs or {})
    if user_id is not None:
        merged.setdefault("user_id", user_id)
    new_state = State(entity_id, state, attributes=merged)
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
    _seed_user(hass, ha_username="pascal")
    config_entry.add_to_hass(hass)
    async_setup_presence(hass, config_entry, client)

    _fire_state(
        hass,
        "person.social_home_test",  # slug is intentionally not the username
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


async def test_pushes_with_resolved_ha_username_not_entity_slug(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Regression: the entity slug can diverge from the auth-provider
    username (HA derives the slug from the display name). The wire
    payload must use the username, not the slug — otherwise the SH
    server's ``presence.username → users(username)`` FK breaks."""
    client = _client()
    _seed_user(hass, ha_username="socialhome")
    config_entry.add_to_hass(hass)
    async_setup_presence(hass, config_entry, client)

    _fire_state(
        hass,
        "person.social_home_test",
        "home",
        {"latitude": 1.0, "longitude": 2.0, "gps_accuracy": 5.0},
    )
    await hass.async_block_till_done()

    kwargs = client.presence.post_location.await_args.kwargs
    assert kwargs["username"] == "socialhome"


async def test_skips_when_person_has_no_user_id(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Manually-tracked person (sensor / device_tracker only) → no SH
    user to push for, skip cleanly."""
    client = _client()
    # No _seed_user — the resolver shouldn't even be called.
    config_entry.add_to_hass(hass)
    async_setup_presence(hass, config_entry, client)

    _fire_state(
        hass,
        "person.guest",
        "home",
        {"latitude": 1.0, "longitude": 2.0},
        user_id=None,
    )
    await hass.async_block_till_done()

    client.presence.post_location.assert_not_awaited()


async def test_skips_when_user_has_no_homeassistant_credential(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """A user that only has non-``homeassistant`` credentials (e.g.
    Trusted Networks, command_line auth, future LDAP) has no
    auth-provider username — skip rather than guess one."""
    client = _client()
    _seed_user(hass, ha_username=None)  # user exists, no HA cred
    config_entry.add_to_hass(hass)
    async_setup_presence(hass, config_entry, client)

    _fire_state(
        hass,
        "person.someone",
        "home",
        {"latitude": 1.0, "longitude": 2.0},
    )
    await hass.async_block_till_done()

    client.presence.post_location.assert_not_awaited()


async def test_high_accuracy_drops_coordinates_keeps_zone(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Above the 500 m cap, latitude/longitude are nulled out."""
    client = _client()
    _seed_user(hass)
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
    _seed_user(hass)
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
    _seed_user(hass)
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
    _seed_user(hass)
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
    _seed_user(hass)
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
    _seed_user(hass)
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
    _seed_user(hass)
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
