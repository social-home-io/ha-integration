"""Static configuration for the Social Home integration.

Kept as plain constants (no runtime logic) so they can be imported
from anywhere in the package — including the config flow, which
HA loads before any entry is set up.
"""

from __future__ import annotations

from typing import Final

from homeassistant.const import Platform

#: HA integration domain. Matches ``manifest.json``.
DOMAIN: Final = "socialhome"

#: Platforms the integration forwards to. Empty in the initial
#: skeleton — entity platforms (sensor, calendar, notify, …) are
#: added one module at a time in follow-up work, and each one only
#: needs to be appended here.
PLATFORMS: Final[list[Platform]] = []

# ── ConfigEntry.data keys ───────────────────────────────────────────────
#
# The config entry persists the instance URL, the user API token,
# and the confirmed identity (user_id + username) returned from
# ``GET /api/me``. The identity is used as the unique_id so re-setup
# against the same Social Home account collapses onto the same
# entry.

CONF_URL: Final = "url"
CONF_TOKEN: Final = "token"
CONF_USER_ID: Final = "user_id"
CONF_USERNAME: Final = "username"

# ── ConfigEntry.options keys ────────────────────────────────────────────
#
# Only the option actually wired today: the presence forwarder.
# Future syncs (calendar, shopping, …) will land their own keys
# alongside the platform that reads them — pinning unused keys
# here invites silent rot and ships a Settings dialog full of
# toggles that don't do anything.

OPT_SYNC_LOCATION: Final = "sync_location"
DEFAULT_SYNC_LOCATION: Final = True
