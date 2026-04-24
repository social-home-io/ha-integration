"""Tests for ``custom_components.social_home.federation``.

Covers the resolve-and-push helper, transient-error swallowing,
and the re-push listener for ``core_config_updated``. These tests
drive the helpers directly rather than going through
``async_setup_entry`` so each behaviour is isolated.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import EVENT_CORE_CONFIG_UPDATE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.network import NoURLAvailableError
from pytest_homeassistant_custom_component.common import MockConfigEntry
from socialhome_client import FederationBaseUpdate, SHClientError

from custom_components.social_home.federation import (
    async_push_federation_base,
    async_register_federation_listener,
)


def _client_with_set_base(
    result: FederationBaseUpdate | Exception,
) -> MagicMock:
    """Build a client mock whose ``federation.set_base`` returns or raises."""
    client = MagicMock()
    client.federation = MagicMock()
    if isinstance(result, Exception):
        client.federation.set_base = AsyncMock(side_effect=result)
    else:
        client.federation.set_base = AsyncMock(return_value=result)
    return client


async def test_push_skipped_when_no_external_url(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No external URL → no push, no log spam, no exception."""
    client = _client_with_set_base(
        FederationBaseUpdate(ok=True, base="", changed=False, peers_notified=0)
    )
    monkeypatch.setattr(
        "custom_components.social_home.federation.get_url",
        MagicMock(side_effect=NoURLAvailableError),
    )

    await async_push_federation_base(hass, client)

    client.federation.set_base.assert_not_awaited()


async def test_push_sends_resolved_url(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client_with_set_base(
        FederationBaseUpdate(
            ok=True,
            base="https://external.example.org",
            changed=True,
            peers_notified=2,
        )
    )
    monkeypatch.setattr(
        "custom_components.social_home.federation.get_url",
        MagicMock(return_value="https://external.example.org"),
    )

    await async_push_federation_base(hass, client)

    client.federation.set_base.assert_awaited_once_with("https://external.example.org")


async def test_push_resolver_requests_external_preferred(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The resolver never asks for internal URLs — those mislead peers."""
    client = _client_with_set_base(
        FederationBaseUpdate(ok=True, base="https://x", changed=False, peers_notified=0)
    )
    resolver = MagicMock(return_value="https://x")
    monkeypatch.setattr("custom_components.social_home.federation.get_url", resolver)

    await async_push_federation_base(hass, client)

    (_args, kwargs) = resolver.call_args
    assert kwargs["allow_internal"] is False
    assert kwargs["allow_external"] is True
    assert kwargs["allow_cloud"] is True
    assert kwargs["prefer_external"] is True


async def test_push_swallows_client_error(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Transient 5xx / connection error is logged and dropped — no raise."""
    client = _client_with_set_base(SHClientError("boom"))
    monkeypatch.setattr(
        "custom_components.social_home.federation.get_url",
        MagicMock(return_value="https://external.example.org"),
    )

    # Must not raise — federation binding is best-effort.
    await async_push_federation_base(hass, client)
    client.federation.set_base.assert_awaited_once()


async def test_listener_repushes_on_core_config_update(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    config_entry: MockConfigEntry,
) -> None:
    """A fresh ``core_config_updated`` event triggers another push."""
    client = _client_with_set_base(
        FederationBaseUpdate(ok=True, base="https://x.test", changed=True, peers_notified=1)
    )
    monkeypatch.setattr(
        "custom_components.social_home.federation.get_url",
        MagicMock(return_value="https://x.test"),
    )

    config_entry.add_to_hass(hass)
    async_register_federation_listener(hass, config_entry, client)

    hass.bus.async_fire(EVENT_CORE_CONFIG_UPDATE, {})
    await hass.async_block_till_done()

    client.federation.set_base.assert_awaited_once_with("https://x.test")
