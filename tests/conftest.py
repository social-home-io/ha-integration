"""Shared fixtures for the Social Home HA custom integration tests.

We lean on ``pytest-homeassistant-custom-component`` for the real
``hass`` fixture. The ``enable_custom_integrations`` auto-use
fixture wires our ``custom_components/social_home`` tree into HA's
component loader so ``MockConfigEntry(domain="social_home")`` picks
it up.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from socialhome_client import FederationBaseUpdate, UnreadSummary, User

from custom_components.social_home.const import (
    CONF_TOKEN,
    CONF_URL,
    CONF_USER_ID,
    CONF_USERNAME,
    DOMAIN,
)

#: Root of the custom-component tree, used by
#: ``pytest-homeassistant-custom-component`` to find the
#: integration under test.
_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> None:
    """Load ``custom_components.social_home`` as a real HA integration."""
    return None


@pytest.fixture
def integration_path() -> Path:
    """Absolute path to the ``custom_components/`` tree.

    Exposed so tests that need to inspect ``manifest.json`` or
    ``strings.json`` don't hard-code a relative path.
    """
    return _ROOT / "custom_components"


@pytest.fixture
def sample_user() -> User:
    return User(
        user_id="user-1",
        username="pascal",
        display_name="Pascal",
        is_admin=True,
    )


@pytest.fixture
def sample_unread() -> UnreadSummary:
    return UnreadSummary(total=3, feed=1, dms=1, spaces={"sp-1": 1})


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """A fully-populated mock config entry for Social Home."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Social Home (pascal)",
        data={
            CONF_URL: "http://sh.test",
            CONF_TOKEN: "token-abc",
            CONF_USER_ID: "user-1",
            CONF_USERNAME: "pascal",
        },
        unique_id="user-1",
    )


@pytest.fixture
def mock_client(sample_user: User, sample_unread: UnreadSummary) -> Iterator[MagicMock]:
    """Patch :class:`SocialHomeClient` across the integration.

    Both ``config_flow`` and ``__init__`` import the client by name,
    so we patch at both import sites. The returned mock exposes the
    resource sub-objects the integration actually calls.
    """
    instance = MagicMock()
    instance.me = MagicMock()
    instance.me.get = AsyncMock(return_value=sample_user)
    instance.me.unread_summary = AsyncMock(return_value=sample_unread)

    # Presence bridge pushes through ``presence.post_location``; give
    # it an awaitable so tests that fire a ``state_changed`` can
    # assert the call without raising ``TypeError: can't be awaited``.
    instance.presence = MagicMock()
    instance.presence.post_location = AsyncMock()

    # Federation helper runs from ``async_setup_entry`` when HA has
    # an external URL; the default stub succeeds with a no-op
    # ``changed=False`` response.
    instance.federation = MagicMock()
    instance.federation.set_base = AsyncMock(
        return_value=FederationBaseUpdate(
            ok=True,
            base="https://external.example.org",
            changed=False,
            peers_notified=0,
        )
    )
    instance.federation.get_base = AsyncMock(return_value=None)

    instance.close = AsyncMock()

    # Async context-manager support: ``async with SocialHomeClient(…)``
    instance.__aenter__ = AsyncMock(return_value=instance)
    instance.__aexit__ = AsyncMock(return_value=None)

    factory = MagicMock(return_value=instance)
    with (
        patch("custom_components.social_home.SocialHomeClient", factory),
        patch("custom_components.social_home.config_flow.SocialHomeClient", factory),
    ):
        # Expose the factory so tests can assert call args; the
        # instance is reachable via ``mock_client.return_value``.
        yield factory


@pytest.fixture
def mock_ws_manager() -> Iterator[MagicMock]:
    """Patch :class:`SocialHomeWsManager` to avoid real WS I/O.

    The coordinator instantiates one on construction; the fake
    no-ops ``connect`` / ``disconnect``.
    """
    instance = MagicMock()
    instance.connect = AsyncMock()
    instance.disconnect = AsyncMock()
    instance.register = MagicMock(return_value=lambda: None)
    factory = MagicMock(return_value=instance)
    with patch("custom_components.social_home.coordinator.SocialHomeWsManager", factory):
        yield factory
