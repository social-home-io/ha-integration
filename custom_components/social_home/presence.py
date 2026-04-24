"""HA → Social Home location push.

Spec §7.3. Subscribes to ``state_changed`` on the HA event bus and,
for ``person.*`` entities, pushes the current location to
``POST /api/presence/location`` on the Social Home instance. This
module deliberately does **not** register any HA entities — it is a
one-way forwarder; `sensor.social_home_presence` is owned by core
and the SH web UI.

Three guards keep the push cheap and safe:

* Accuracy cap (:data:`_ACCURACY_CAP_M`) — if HA reports
  ``gps_accuracy`` worse than this, the coordinates are dropped
  (still push the zone name). Prevents a jittery phone from
  flooding the federation with useless moves.
* Distance dedup (:data:`_DEDUP_METRES`) — skip if the user has
  moved less than this since the last push. Measured with a
  haversine great-circle distance.
* 4-decimal-place truncation — hard cap on precision before the
  wire, independently of what HA reported. The server truncates
  again defensively; doing it here means nothing tighter than
  ~11 m leaves this process in the first place.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, EventStateChangedData, HomeAssistant
from socialhome_client import SHClientError, SocialHomeClient

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)

#: HA emits ``person.<username>``; strip this prefix to get the SH username.
_PERSON_PREFIX = "person."

#: Above this accuracy (in metres) the location is treated as "no
#: useful fix" — we still push the zone so automations keep working.
_ACCURACY_CAP_M = 500.0

#: Don't push a new location if the user hasn't moved at least this
#: far since the last push. Large enough to absorb GPS jitter, small
#: enough to track walking-speed movement.
_DEDUP_METRES = 50.0

#: Mean Earth radius used in the haversine calculation.
_EARTH_RADIUS_M = 6_371_000.0

#: HA state values that mean "no known zone" — coerce them to ``None``
#: before sending so the server doesn't store "unavailable" as a place.
_UNKNOWN_ZONE_STATES = frozenset({"not_home", "unknown", "unavailable", "none"})


def _haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in metres between two ``(lat, lon)`` pairs."""
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(h))


def async_setup_presence(hass: HomeAssistant, entry: ConfigEntry, client: SocialHomeClient) -> None:
    """Subscribe to ``state_changed`` and forward person updates.

    The unsubscribe hook is registered on ``entry`` so
    ``async_unload_entry`` tears the listener down automatically.

    Per-username dedup state lives in the closure's ``last_loc``
    dict — a single ``async_setup_entry`` call owns one dict; a
    reload gets a fresh one.
    """
    last_loc: dict[str, tuple[float, float]] = {}

    async def _on_state_changed(event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        if not entity_id.startswith(_PERSON_PREFIX):
            return
        new_state = event.data["new_state"]
        if new_state is None:
            return

        username = entity_id[len(_PERSON_PREFIX) :]
        attrs = new_state.attributes
        lat_raw = attrs.get("latitude")
        lon_raw = attrs.get("longitude")
        acc_raw = attrs.get("gps_accuracy")
        state = new_state.state

        lat = float(lat_raw) if lat_raw is not None else None
        lon = float(lon_raw) if lon_raw is not None else None
        acc = float(acc_raw) if acc_raw is not None else None

        # Accuracy cap — drop coords but keep the zone so automations
        # that key off zone transitions continue to work.
        if acc is not None and acc > _ACCURACY_CAP_M:
            lat = lon = None

        # Movement dedup — cheap exit before the HTTP round-trip.
        if lat is not None and lon is not None:
            previous = last_loc.get(username)
            if previous is not None and _haversine_m(previous, (lat, lon)) < _DEDUP_METRES:
                return
            last_loc[username] = (lat, lon)

        # 4dp truncation — hard privacy cap before the wire.
        if lat is not None:
            lat = round(lat, 4)
        if lon is not None:
            lon = round(lon, 4)

        zone_name = state if state and state not in _UNKNOWN_ZONE_STATES else None

        try:
            await client.presence.post_location(
                username=username,
                latitude=lat,
                longitude=lon,
                accuracy_m=acc,
                zone_name=zone_name,
            )
        except SHClientError as err:
            # Pushes are best-effort: the next ``state_changed`` will
            # retry naturally. We log at debug so a flaky network
            # doesn't flood warnings on every minor person update.
            _LOGGER.debug("Social Home: location push for %s failed: %s", username, err)

    entry.async_on_unload(hass.bus.async_listen(EVENT_STATE_CHANGED, _on_state_changed))
