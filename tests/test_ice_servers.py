"""Tests for ``custom_components.social_home.ice_servers``.

Covers the resolve-and-push helper, transient-error swallowing,
and the re-push listener for ``core_config_updated``. Drives the
helpers directly rather than going through ``async_setup_entry``
so each behaviour is isolated, mirroring ``test_federation.py``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import EVENT_CORE_CONFIG_UPDATE
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from socialhome_client import IceServer, IceServersUpdate, SHClientError
from webrtc_models import RTCIceServer

from custom_components.social_home.ice_servers import (
    async_push_ice_servers,
    async_register_ice_servers_listener,
)


def _client_with_set_ice_servers(
    result: IceServersUpdate | Exception,
) -> MagicMock:
    """Build a client mock whose ``ha.set_ice_servers`` returns or raises."""
    client = MagicMock()
    client.ha = MagicMock()
    if isinstance(result, Exception):
        client.ha.set_ice_servers = AsyncMock(side_effect=result)
    else:
        client.ha.set_ice_servers = AsyncMock(return_value=result)
    return client


def _ok_update(servers: list[dict[str, Any]] | None = None) -> IceServersUpdate:
    payload = servers if servers is not None else [{"urls": ["stun:stun.test:3478"]}]
    return IceServersUpdate(
        ok=True,
        ice_servers=tuple(IceServer.from_api(s) for s in payload),
        changed=True,
    )


async def test_push_skipped_when_no_servers(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty HA list → no push, no log spam, no exception."""
    client = _client_with_set_ice_servers(_ok_update())
    monkeypatch.setattr(
        "custom_components.social_home.ice_servers.async_get_ice_servers",
        MagicMock(return_value=[]),
    )

    await async_push_ice_servers(hass, client)

    client.ha.set_ice_servers.assert_not_awaited()


async def test_push_sends_serialised_servers(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The resolved list is serialised via ``RTCIceServer.to_dict()``."""
    servers = [
        RTCIceServer(urls=["stun:stun.test:3478"]),
        RTCIceServer(
            urls=["turn:turn.test:3478"],
            username="alice",
            credential="s3cret",
        ),
    ]
    client = _client_with_set_ice_servers(_ok_update())
    monkeypatch.setattr(
        "custom_components.social_home.ice_servers.async_get_ice_servers",
        MagicMock(return_value=servers),
    )

    await async_push_ice_servers(hass, client)

    client.ha.set_ice_servers.assert_awaited_once()
    (sent,) = client.ha.set_ice_servers.await_args.args
    # First entry: STUN-only, no auth fields in the dict.
    assert sent[0] == {"urls": ["stun:stun.test:3478"]}
    # Second entry: TURN with credentials carried through unchanged.
    assert sent[1]["urls"] == ["turn:turn.test:3478"]
    assert sent[1]["username"] == "alice"
    assert sent[1]["credential"] == "s3cret"


async def test_push_uses_web_rtc_public_api(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The helper delegates to ``web_rtc.async_get_ice_servers``."""
    resolver = MagicMock(return_value=[RTCIceServer(urls=["stun:x.test"])])
    client = _client_with_set_ice_servers(_ok_update())
    monkeypatch.setattr("custom_components.social_home.ice_servers.async_get_ice_servers", resolver)

    await async_push_ice_servers(hass, client)

    resolver.assert_called_once_with(hass)


async def test_push_swallows_client_error(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Transient 5xx / connection error is logged and dropped — no raise."""
    client = _client_with_set_ice_servers(SHClientError("boom"))
    monkeypatch.setattr(
        "custom_components.social_home.ice_servers.async_get_ice_servers",
        MagicMock(return_value=[RTCIceServer(urls=["stun:x.test"])]),
    )

    # Must not raise — ICE-server binding is best-effort.
    await async_push_ice_servers(hass, client)
    client.ha.set_ice_servers.assert_awaited_once()


async def test_listener_repushes_on_core_config_update(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    config_entry: MockConfigEntry,
) -> None:
    """A fresh ``core_config_updated`` event triggers another push."""
    client = _client_with_set_ice_servers(_ok_update())
    monkeypatch.setattr(
        "custom_components.social_home.ice_servers.async_get_ice_servers",
        MagicMock(return_value=[RTCIceServer(urls=["stun:x.test"])]),
    )

    config_entry.add_to_hass(hass)
    async_register_ice_servers_listener(hass, config_entry, client)

    hass.bus.async_fire(EVENT_CORE_CONFIG_UPDATE, {})
    await hass.async_block_till_done()

    client.ha.set_ice_servers.assert_awaited_once()


async def test_push_logs_count_when_changed(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A successful, changed push surfaces an INFO log with the count."""
    client = _client_with_set_ice_servers(
        _ok_update([{"urls": ["stun:a.test"]}, {"urls": ["stun:b.test"]}])
    )
    monkeypatch.setattr(
        "custom_components.social_home.ice_servers.async_get_ice_servers",
        MagicMock(return_value=[RTCIceServer(urls=["stun:a.test"])]),
    )

    with caplog.at_level("INFO", logger="custom_components.social_home.ice_servers"):
        await async_push_ice_servers(hass, client)

    assert any("ICE servers updated" in rec.message for rec in caplog.records)


async def test_push_quiet_when_unchanged(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``changed=False`` → no INFO log, just the silent no-op."""
    client = _client_with_set_ice_servers(
        IceServersUpdate(
            ok=True,
            ice_servers=(IceServer(urls=("stun:a.test",)),),
            changed=False,
        )
    )
    monkeypatch.setattr(
        "custom_components.social_home.ice_servers.async_get_ice_servers",
        MagicMock(return_value=[RTCIceServer(urls=["stun:a.test"])]),
    )

    with caplog.at_level("INFO", logger="custom_components.social_home.ice_servers"):
        await async_push_ice_servers(hass, client)

    assert not any("ICE servers updated" in rec.message for rec in caplog.records)
