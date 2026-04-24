"""Config flow for the Social Home integration.

Spec §7.2. Three entry paths land the same config entry shape:

* User flow — manual URL + token (standalone / Docker deployments).
* Hassio discovery — zero-config when Social Home is installed as
  an HA App; the Supervisor pushes a URL and token to HA.
* Re-auth — HA invokes this when the coordinator raises
  :class:`ConfigEntryAuthFailed` (typically a revoked token).

The options flow is trivial; it just persists on/off toggles for
sync features so future platform modules can read them without
re-designing the surface.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from aiohasupervisor.exceptions import SupervisorError
from homeassistant.components.hassio import get_supervisor_client
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.service_info.hassio import HassioServiceInfo
from socialhome_client import SHAuthError, SHClientError, SocialHomeClient

from .const import (
    ADDON_HTTP_PORT,
    CONF_TOKEN,
    CONF_URL,
    CONF_USER_ID,
    CONF_USERNAME,
    DEFAULT_SYNC_CALENDAR,
    DEFAULT_SYNC_LOCATION,
    DEFAULT_SYNC_SHOPPING,
    DEFAULT_SYNC_SPACE_CALENDARS,
    DOMAIN,
    OPT_SYNC_CALENDAR,
    OPT_SYNC_LOCATION,
    OPT_SYNC_SHOPPING,
    OPT_SYNC_SPACE_CALENDARS,
)

_LOGGER = logging.getLogger(__name__)

#: User flow schema — URL + token, both required. ``vol.Url`` only
#: validates shape (scheme + host); the real validation is the
#: ``GET /api/me`` round-trip.
USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): str,
        vol.Required(CONF_TOKEN): str,
    }
)

#: Re-auth schema — URL is locked (it stays whatever the entry
#: already has); the user only rotates the token.
REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_TOKEN): str})


async def _validate(url: str, token: str) -> dict[str, str]:
    """Round-trip ``GET /api/me`` to confirm credentials.

    Returns the fields that should be merged into the entry's data
    on success. Raises :class:`SHAuthError` / :class:`SHClientError`
    on failure so callers can map to form errors.
    """
    async with SocialHomeClient(url, token) as client:
        me = await client.me.get()
    return {CONF_USER_ID: me.user_id, CONF_USERNAME: me.username}


async def _resolve_addon_url(hass: HomeAssistant, slug: str) -> str:
    """Resolve the internal HA App URL from its add-on slug.

    The Social Home add-on runs inside the Supervisor's private
    network; its container is reachable at the hostname reported
    by the Supervisor (usually ``core-<slug>`` or the slug
    verbatim) on :data:`ADDON_HTTP_PORT`. We fetch that hostname
    via the Supervisor client instead of hard-coding a pattern, so
    an add-on rename never breaks the integration.

    Raises :class:`SHClientError` so :meth:`async_step_hassio` can
    map any failure back to ``cannot_connect``.
    """
    supervisor = get_supervisor_client(hass)
    try:
        info = await supervisor.addons.addon_info(slug)
    except SupervisorError as err:
        _LOGGER.debug("Social Home: Supervisor lookup for %s failed: %s", slug, err)
        raise SHClientError(f"could not resolve Social Home add-on {slug}: {err}") from err

    hostname = getattr(info, "hostname", None)
    if not hostname:
        raise SHClientError(f"Social Home add-on {slug} has no resolvable hostname")
    return f"http://{hostname}:{ADDON_HTTP_PORT}"


class SocialHomeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handles add-integration, Supervisor discovery, and re-auth."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered: dict[str, Any] = {}

    # ── Manual setup ────────────────────────────────────────────────────

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """URL + token form used by Docker / bare-metal deployments."""
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL]
            token = user_input[CONF_TOKEN]
            try:
                identity = await _validate(url, token)
            except SHAuthError:
                errors[CONF_TOKEN] = "invalid_auth"
            except SHClientError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(identity[CONF_USER_ID])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Social Home ({identity[CONF_USERNAME]})",
                    data={CONF_URL: url, CONF_TOKEN: token, **identity},
                )
        return self.async_show_form(step_id="user", data_schema=USER_SCHEMA, errors=errors)

    # ── Zero-config via Supervisor ──────────────────────────────────────

    async def async_step_hassio(self, discovery_info: HassioServiceInfo) -> ConfigFlowResult:
        """Supervisor pushed a discovery record for the HA App container.

        The core server publishes a minimal payload
        (``{"service": "socialhome", "config": {"token": …}}``) and
        relies on HA to resolve the add-on's internal hostname from
        the slug — the URL is never wire-visible for a local install.
        We also accept a pre-resolved ``url`` in the payload so the
        flow stays compatible with any future change that has core
        publish the full URL itself.
        """
        token = str(discovery_info.config.get("token") or "")
        if not token:
            return self.async_abort(reason="cannot_connect")

        url = discovery_info.config.get("url")
        if not url:
            try:
                url = await _resolve_addon_url(self.hass, discovery_info.slug)
            except SHClientError:
                return self.async_abort(reason="cannot_connect")

        try:
            identity = await _validate(url, token)
        except SHClientError:
            return self.async_abort(reason="cannot_connect")

        await self.async_set_unique_id(identity[CONF_USER_ID])
        self._abort_if_unique_id_configured(updates={CONF_URL: url, CONF_TOKEN: token})
        self._discovered = {
            CONF_URL: url,
            CONF_TOKEN: token,
            **identity,
        }
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Single-button confirm screen for App auto-discovery."""
        if user_input is not None:
            return self.async_create_entry(
                title=f"Social Home ({self._discovered[CONF_USERNAME]})",
                data=self._discovered,
            )
        return self.async_show_form(
            step_id="hassio_confirm",
            description_placeholders={"username": self._discovered[CONF_USERNAME]},
        )

    # ── Re-auth (revoked / expired token) ───────────────────────────────

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """HA invokes this when the coordinator raises auth-failed."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a fresh token; URL stays pinned to the entry."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_TOKEN]
            try:
                await _validate(entry.data[CONF_URL], token)
            except SHAuthError:
                errors[CONF_TOKEN] = "invalid_auth"
            except SHClientError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(entry, data_updates={CONF_TOKEN: token})
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=REAUTH_SCHEMA, errors=errors
        )

    def _get_reauth_entry(self) -> ConfigEntry:
        """Return the entry being re-authed — guaranteed when the
        flow's source is ``SOURCE_REAUTH``."""
        if self.source != SOURCE_REAUTH:
            raise RuntimeError("reauth entry requested outside re-auth flow")
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry is None:
            raise RuntimeError("re-auth context missing its config entry")
        return entry

    # ── Options flow ────────────────────────────────────────────────────

    @staticmethod
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return SocialHomeOptionsFlow()


class SocialHomeOptionsFlow(OptionsFlow):
    """Lets the user toggle what the integration syncs.

    The skeleton defines the keys and defaults even though no
    platform reads them yet — pinning the schema now means
    user-saved options survive future platform additions.
    """

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    OPT_SYNC_LOCATION,
                    default=current.get(OPT_SYNC_LOCATION, DEFAULT_SYNC_LOCATION),
                ): bool,
                vol.Optional(
                    OPT_SYNC_CALENDAR,
                    default=current.get(OPT_SYNC_CALENDAR, DEFAULT_SYNC_CALENDAR),
                ): bool,
                vol.Optional(
                    OPT_SYNC_SPACE_CALENDARS,
                    default=current.get(OPT_SYNC_SPACE_CALENDARS, DEFAULT_SYNC_SPACE_CALENDARS),
                ): bool,
                vol.Optional(
                    OPT_SYNC_SHOPPING,
                    default=current.get(OPT_SYNC_SHOPPING, DEFAULT_SYNC_SHOPPING),
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
