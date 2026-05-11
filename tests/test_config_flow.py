"""Tests for ``custom_components.socialhome.config_flow``.

Covers all four entry paths (user, hassio, reauth, options) and
their error branches so every form error string in ``strings.json``
is exercised at least once.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.hassio import HassioServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry
from socialhome_client import SHAuthError, SHClientError

from custom_components.socialhome.const import (
    CONF_TOKEN,
    CONF_URL,
    CONF_USER_ID,
    CONF_USERNAME,
    DOMAIN,
    OPT_SYNC_LOCATION,
)


def _hassio_info(
    *,
    host: str = "local-social-home",
    port: int = 8099,
    token: str = "tok",
    slug: str = "local_social_home",
) -> HassioServiceInfo:
    """Build a hassio discovery payload.

    Matches the payload the add-on publishes starting with
    ``socialhome >= 2026.5.11`` —
    ``{"service": "socialhome", "config": {"host": …, "port": …, "token": …}}``.
    Defaults reflect what the Supervisor reports for the stable
    add-on (``/addons/local_social_home/info``).
    """
    return HassioServiceInfo(
        config={"host": host, "port": port, "token": token},
        name="Social Home",
        slug=slug,
        uuid="00000000-0000-0000-0000-000000000001",
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


async def test_hassio_flow_creates_entry_from_payload(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """The add-on advertises host + port + token; all three flow
    into the config entry untouched."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_HASSIO},
        data=_hassio_info(host="local-social-home", port=8099, token="tok"),
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "hassio_confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_URL: "http://local-social-home:8099",
        CONF_TOKEN: "tok",
        CONF_USER_ID: "user-1",
        CONF_USERNAME: "pascal",
    }


async def test_hassio_flow_honours_payload_host_and_port(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """A non-default host/port pair (e.g. the early-access add-on
    with a custom ``listen_port``) is used verbatim — no
    substitution, no fallback constants."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_HASSIO},
        data=_hassio_info(
            host="local-social-home-early",
            port=18099,
            token="tok",
            slug="local_social_home_early",
        ),
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_URL] == "http://local-social-home-early:18099"


async def test_hassio_flow_aborts_when_host_missing(hass: HomeAssistant) -> None:
    """Old add-on releases that pre-date the ``host``/``port`` payload
    have no useful URL to validate — surface that as
    ``cannot_connect`` rather than guessing."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_HASSIO},
        data=HassioServiceInfo(
            config={"port": 8099, "token": "tok"},
            name="Social Home",
            slug="local_social_home",
            uuid="00000000-0000-0000-0000-000000000004",
        ),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_hassio_flow_aborts_when_port_missing(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_HASSIO},
        data=HassioServiceInfo(
            config={"host": "local-social-home", "token": "tok"},
            name="Social Home",
            slug="local_social_home",
            uuid="00000000-0000-0000-0000-000000000005",
        ),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_hassio_flow_aborts_when_token_missing(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_HASSIO},
        data=HassioServiceInfo(
            config={"host": "local-social-home", "port": 8099},
            name="Social Home",
            slug="local_social_home",
            uuid="00000000-0000-0000-0000-000000000003",
        ),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_hassio_flow_aborts_on_connect_error(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    mock_client.return_value.me.get = AsyncMock(side_effect=SHClientError("boom"))

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_HASSIO},
        data=_hassio_info(),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_hassio_flow_updates_existing_entry(
    hass: HomeAssistant,
    mock_client: MagicMock,
    config_entry: MockConfigEntry,
) -> None:
    """Re-discovery swaps the URL (host + port from the payload)
    and the rotated token on the already-configured entry."""
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_HASSIO},
        data=_hassio_info(host="local-social-home", port=8099, token="rotated"),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert config_entry.data[CONF_URL] == "http://local-social-home:8099"
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


async def test_options_flow_saves_toggle(
    hass: HomeAssistant,
    mock_client: MagicMock,
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
        {OPT_SYNC_LOCATION: False},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options[OPT_SYNC_LOCATION] is False
