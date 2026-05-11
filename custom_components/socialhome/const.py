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
# Options drive what the integration syncs. The skeleton declares
# the keys so the options flow + future platform modules agree on
# spelling; platform code reads them on setup.

OPT_SYNC_LOCATION: Final = "sync_location"
OPT_SYNC_CALENDAR: Final = "sync_calendar"
OPT_SYNC_SPACE_CALENDARS: Final = "sync_space_calendars"
OPT_SYNC_SHOPPING: Final = "sync_shopping"

DEFAULT_SYNC_LOCATION: Final = True
DEFAULT_SYNC_CALENDAR: Final = True
DEFAULT_SYNC_SPACE_CALENDARS: Final = False
DEFAULT_SYNC_SHOPPING: Final = True

# ── Hassio discovery ────────────────────────────────────────────────────

#: Port the Social Home add-on's HTTP server listens on inside its
#: container — matches ``listen_port`` in the upstream
#: ``socialhome.config`` defaults. The Supervisor exposes the
#: add-on's container under its slug on the hassio Docker network,
#: so the integration reaches it at
#: ``http://<addon_slug>:ADDON_HTTP_PORT`` without needing the
#: Supervisor to publish a URL in the discovery payload.
ADDON_HTTP_PORT: Final = 8099

# ── Coordinator tuning ──────────────────────────────────────────────────

#: How often the shared coordinator polls
#: ``GET /api/me/unread-summary``. Spec §6.2a. WS events can trigger
#: an immediate refresh between polls.
UPDATE_INTERVAL_SECONDS: Final = 60
