"""Tests for ``custom_components.social_home.federation_inbox``.

The view is an HTTP passthrough — each test drives it through a
real aiohttp test client (``hass_client_no_auth``) and asserts the
mirrored response comes back untouched.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator
from socialhome_client import FederationRelayResult, SHClientError


async def _setup(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_client: MagicMock,
    mock_ws_manager: MagicMock,
) -> None:
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()


async def test_inbox_post_mirrors_addon_response(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_client: MagicMock,
    mock_ws_manager: MagicMock,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """Happy path: raw body forwarded; status + body + content-type echoed."""
    mock_client.return_value.federation.forward_inbox_envelope = AsyncMock(
        return_value=FederationRelayResult(
            status=200, body=b'{"status":"ok"}', content_type="application/json"
        )
    )
    await _setup(hass, config_entry, mock_client, mock_ws_manager)
    client = await hass_client_no_auth()

    resp = await client.post(
        "/api/social_home/inbox/wh-peer",
        data=b'{"msg_id":"m1"}',
        headers={"Content-Type": "application/octet-stream"},
    )

    assert resp.status == 200
    assert await resp.read() == b'{"status":"ok"}'
    assert resp.headers["Content-Type"].startswith("application/json")
    mock_client.return_value.federation.forward_inbox_envelope.assert_awaited_once()
    (inbox_id, body), kwargs = (
        mock_client.return_value.federation.forward_inbox_envelope.call_args.args,
        mock_client.return_value.federation.forward_inbox_envelope.call_args.kwargs,
    )
    assert inbox_id == "wh-peer"
    assert body == b'{"msg_id":"m1"}'
    assert kwargs["extra_headers"] is None


async def test_inbox_post_passes_signature_header(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_client: MagicMock,
    mock_ws_manager: MagicMock,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    mock_client.return_value.federation.forward_inbox_envelope = AsyncMock(
        return_value=FederationRelayResult(status=200, body=b"", content_type="")
    )
    await _setup(hass, config_entry, mock_client, mock_ws_manager)
    client = await hass_client_no_auth()

    await client.post(
        "/api/social_home/inbox/wh-peer",
        data=b"{}",
        headers={"X-SocialHome-Signature": "ed25:abc"},
    )

    kwargs = mock_client.return_value.federation.forward_inbox_envelope.call_args.kwargs
    assert kwargs["extra_headers"] == {"X-SocialHome-Signature": "ed25:abc"}


async def test_inbox_post_non_2xx_is_passed_through(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_client: MagicMock,
    mock_ws_manager: MagicMock,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """410 from the add-on (replay / expired) surfaces as 410 to the peer."""
    mock_client.return_value.federation.forward_inbox_envelope = AsyncMock(
        return_value=FederationRelayResult(
            status=410,
            body=b'{"error":"Replay detected"}',
            content_type="application/json",
        )
    )
    await _setup(hass, config_entry, mock_client, mock_ws_manager)
    client = await hass_client_no_auth()

    resp = await client.post("/api/social_home/inbox/wh-peer", data=b"{}")

    assert resp.status == 410
    assert await resp.read() == b'{"error":"Replay detected"}'


async def test_inbox_post_forwards_large_body_to_addon(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_client: MagicMock,
    mock_ws_manager: MagicMock,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """The inbox doubles as a WebRTC fallback for chunked media — no local cap.

    The add-on is the policy point for envelope / chunk size; the
    integration is a pure passthrough so a large body lands on the
    relay verbatim.
    """
    captured: dict[str, bytes] = {}

    async def _record(inbox_id: str, body: bytes, *, extra_headers=None) -> FederationRelayResult:
        captured["inbox_id"] = inbox_id.encode()
        captured["body"] = body
        return FederationRelayResult(status=200, body=b"", content_type="")

    mock_client.return_value.federation.forward_inbox_envelope = AsyncMock(side_effect=_record)
    await _setup(hass, config_entry, mock_client, mock_ws_manager)
    client = await hass_client_no_auth()

    # 2 MiB — comfortably above the spec's old envelope-only cap,
    # well below HA's ``client_max_size`` (16 MiB default).
    big = b"x" * (2 * 1024 * 1024)
    resp = await client.post("/api/social_home/inbox/wh-peer", data=big)

    assert resp.status == 200
    assert captured["inbox_id"] == b"wh-peer"
    assert captured["body"] == big


async def test_inbox_post_maps_client_error_to_bad_gateway(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_client: MagicMock,
    mock_ws_manager: MagicMock,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    mock_client.return_value.federation.forward_inbox_envelope = AsyncMock(
        side_effect=SHClientError("dns failure")
    )
    await _setup(hass, config_entry, mock_client, mock_ws_manager)
    client = await hass_client_no_auth()

    resp = await client.post("/api/social_home/inbox/wh-peer", data=b"{}")
    assert resp.status == 502


async def test_inbox_view_requires_no_auth(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_client: MagicMock,
    mock_ws_manager: MagicMock,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """Signed envelope is the auth; the HA endpoint must not require a bearer."""
    mock_client.return_value.federation.forward_inbox_envelope = AsyncMock(
        return_value=FederationRelayResult(status=200, body=b"", content_type="")
    )
    await _setup(hass, config_entry, mock_client, mock_ws_manager)
    # ``hass_client_no_auth`` has no Authorization header. A 401
    # would mean the view was wired with ``requires_auth`` left on
    # its default. Expect 200.
    client = await hass_client_no_auth()
    resp = await client.post("/api/social_home/inbox/wh-peer", data=b"{}")
    assert resp.status == 200


async def test_inbox_view_registered_once_across_reloads(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_client: MagicMock,
    mock_ws_manager: MagicMock,
) -> None:
    """Reloading the entry must not raise on duplicate URL registration."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    # Second setup cycle — same URL, would 500 if register_view were
    # called twice.
    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
