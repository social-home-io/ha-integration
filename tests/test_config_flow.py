"""Tests for ``custom_components.social_home.config_flow``.

Covers all four entry paths (user, hassio, reauth, options) and
their error branches so every form error string in ``strings.json``
is exercised at least once.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from aiohasupervisor.exceptions import SupervisorError
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.hassio import HassioServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry
from socialhome_client import SHAuthError, SHClientError

from custom_components.social_home.const import (
    CONF_TOKEN,
    CONF_URL,
    CONF_USER_ID,
    CONF_USERNAME,
    DOMAIN,
    OPT_SYNC_LOCATION,
    OPT_SYNC_SHOPPING,
    OPT_SYNC_SPACE_CALENDARS,
)


def _hassio_info(
    *,
    token: str = "tok",
    url: str | None = None,
    slug: str = "social_home",
) -> HassioServiceInfo:
    """Build a hassio discovery payload.

    Matches what the core server actually posts to the Supervisor
    (``{"token": …}`` only) — pass ``url`` to exercise the future
    spec-aligned shape that also includes a pre-resolved URL.
    """
    config: dict[str, str] = {"token": token}
    if url is not None:
        config["url"] = url
    return HassioServiceInfo(
        config=config,
        name="Social Home",
        slug=slug,
        uuid="00000000-0000-0000-0000-000000000001",
    )


def _patch_supervisor_resolver(hostname: str | SupervisorError):
    """Patch the Supervisor client so ``addon_info`` returns ``hostname``.

    Pass a :class:`SupervisorError` to simulate a Supervisor-side
    failure; anything else is used as the resolved internal
    hostname string.
    """
    if isinstance(hostname, SupervisorError):
        addon_info = AsyncMock(side_effect=hostname)
    else:
        info = MagicMock()
        info.hostname = hostname
        addon_info = AsyncMock(return_value=info)

    supervisor = MagicMock()
    supervisor.addons = MagicMock()
    supervisor.addons.addon_info = addon_info
    return patch(
        "custom_components.social_home.config_flow.get_supervisor_client",
        return_value=supervisor,
    )


# ── User flow ─────────────────────────────────────────────────────────────


async def test_user_flow_success(hass: HomeAssistant, mock_client: MagicMock) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_URL: "http://sh.test", CONF_TOKEN: "token-abc"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Social Home (pascal)"
    assert result["data"] == {
        CONF_URL: "http://sh.test",
        CONF_TOKEN: "token-abc",
        CONF_USER_ID: "user-1",
        CONF_USERNAME: "pascal",
    }
    assert result["result"].unique_id == "user-1"
    # Flow validation + auto-setup both instantiate a client against the
    # same URL + token; assert the identity, not the call count.
    mock_client.assert_any_call("http://sh.test", "token-abc")


async def test_user_flow_invalid_auth(hass: HomeAssistant, mock_client: MagicMock) -> None:
    mock_client.return_value.me.get = AsyncMock(side_effect=SHAuthError())

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_URL: "http://sh.test", CONF_TOKEN: "bad"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_TOKEN: "invalid_auth"}


async def test_user_flow_cannot_connect(hass: HomeAssistant, mock_client: MagicMock) -> None:
    mock_client.return_value.me.get = AsyncMock(side_effect=SHClientError("dns"))

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_URL: "http://sh.test", CONF_TOKEN: "tok"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_duplicate_is_aborted(
    hass: HomeAssistant,
    mock_client: MagicMock,
    config_entry: MockConfigEntry,
) -> None:
    """Same ``user_id`` → single-instance guard aborts the flow."""
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_URL: "http://sh.test", CONF_TOKEN: "token-abc"},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


# ── Hassio discovery ──────────────────────────────────────────────────────


async def test_hassio_flow_resolves_url_from_slug(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """Core-shape payload (token only): resolve URL via Supervisor."""
    with _patch_supervisor_resolver("core-socialhome"):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_HASSIO},
            data=_hassio_info(token="tok"),
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "hassio_confirm"

        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_URL: "http://core-socialhome:8099",
        CONF_TOKEN: "tok",
        CONF_USER_ID: "user-1",
        CONF_USERNAME: "pascal",
    }


async def test_hassio_flow_accepts_prerolled_url(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """Spec-form payload with a pre-resolved URL skips the Supervisor lookup."""
    with _patch_supervisor_resolver(SupervisorError("would fail if called")):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_HASSIO},
            data=_hassio_info(token="tok", url="http://sh.test"),
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_URL] == "http://sh.test"


async def test_hassio_flow_aborts_when_token_missing(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_HASSIO},
        data=HassioServiceInfo(
            config={},
            name="Social Home",
            slug="social_home",
            uuid="00000000-0000-0000-0000-000000000002",
        ),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_hassio_flow_aborts_when_supervisor_lookup_fails(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    with _patch_supervisor_resolver(SupervisorError("no such addon")):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_HASSIO},
            data=_hassio_info(token="tok"),
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_hassio_flow_aborts_on_connect_error(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    mock_client.return_value.me.get = AsyncMock(side_effect=SHClientError("boom"))

    with _patch_supervisor_resolver("core-socialhome"):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_HASSIO},
            data=_hassio_info(token="tok"),
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_hassio_flow_updates_existing_entry(
    hass: HomeAssistant,
    mock_client: MagicMock,
    config_entry: MockConfigEntry,
) -> None:
    """Re-discovery swaps URL + token on the already-configured entry."""
    config_entry.add_to_hass(hass)

    with _patch_supervisor_resolver("core-socialhome-new"):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_HASSIO},
            data=_hassio_info(token="rotated"),
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert config_entry.data[CONF_URL] == "http://core-socialhome-new:8099"
    assert config_entry.data[CONF_TOKEN] == "rotated"


# ── Re-auth ───────────────────────────────────────────────────────────────


async def test_reauth_flow_success(
    hass: HomeAssistant,
    mock_client: MagicMock,
    config_entry: MockConfigEntry,
) -> None:
    config_entry.add_to_hass(hass)
    flow_result = await config_entry.start_reauth_flow(hass)
    assert flow_result["type"] is FlowResultType.FORM
    assert flow_result["step_id"] == "reauth_confirm"

    flow_result = await hass.config_entries.flow.async_configure(
        flow_result["flow_id"], {CONF_TOKEN: "fresh-token"}
    )
    assert flow_result["type"] is FlowResultType.ABORT
    assert flow_result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_TOKEN] == "fresh-token"


async def test_reauth_flow_invalid_token(
    hass: HomeAssistant,
    mock_client: MagicMock,
    config_entry: MockConfigEntry,
) -> None:
    config_entry.add_to_hass(hass)
    flow_result = await config_entry.start_reauth_flow(hass)
    mock_client.return_value.me.get = AsyncMock(side_effect=SHAuthError())

    flow_result = await hass.config_entries.flow.async_configure(
        flow_result["flow_id"], {CONF_TOKEN: "still-bad"}
    )
    assert flow_result["type"] is FlowResultType.FORM
    assert flow_result["errors"] == {CONF_TOKEN: "invalid_auth"}


async def test_reauth_flow_connection_error(
    hass: HomeAssistant,
    mock_client: MagicMock,
    config_entry: MockConfigEntry,
) -> None:
    config_entry.add_to_hass(hass)
    flow_result = await config_entry.start_reauth_flow(hass)
    mock_client.return_value.me.get = AsyncMock(side_effect=SHClientError("net"))

    flow_result = await hass.config_entries.flow.async_configure(
        flow_result["flow_id"], {CONF_TOKEN: "any"}
    )
    assert flow_result["type"] is FlowResultType.FORM
    assert flow_result["errors"] == {"base": "cannot_connect"}


# ── Options flow ──────────────────────────────────────────────────────────


async def test_options_flow_saves_toggles(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_ws_manager: MagicMock,
    config_entry: MockConfigEntry,
) -> None:
    config_entry.add_to_hass(hass)
    # Set up the entry so the options flow has a real context.
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            OPT_SYNC_LOCATION: False,
            OPT_SYNC_SHOPPING: True,
            OPT_SYNC_SPACE_CALENDARS: True,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    # Options are persisted on the entry.
    assert config_entry.options[OPT_SYNC_LOCATION] is False
    assert config_entry.options[OPT_SYNC_SPACE_CALENDARS] is True
