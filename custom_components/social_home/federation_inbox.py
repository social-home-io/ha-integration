"""Public federation inbox endpoint.

Spec §7.10. Other Social Home instances can't reach the add-on
container directly — it sits behind the Supervisor's private
network. The integration therefore exposes a public URL on the
Home Assistant HTTP server, and when a peer POSTs a federation
envelope to it we proxy the raw bytes to the add-on's internal
``/federation/inbox/{inbox_id}`` and mirror the response back.

This module does not decrypt, validate, or even JSON-parse the
envelope: the add-on runs the full §24.11 validation pipeline, so
any crypto in the integration would only add latency + risk. The
Ed25519 signature sitting inside the envelope body is the auth —
that's why ``requires_auth = False`` on the view.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from socialhome_client import SHClientError

from .const import DOMAIN

if TYPE_CHECKING:
    from . import SocialHomeRuntimeData

_LOGGER = logging.getLogger(__name__)

#: Mirror the add-on's 1 MiB inbound-envelope cap. Oversize payloads
#: are rejected here so we don't waste bandwidth on the internal
#: hop + let the add-on's own limiter do the final check anyway.
_MAX_ENVELOPE_BYTES: Final = 1 * 1024 * 1024

#: HA keeps view registration idempotent via a flag in
#: ``hass.data[DOMAIN]``. Reloading a single config entry must not
#: re-register the same URL; tearing down the last entry clears
#: the flag so a future setup re-attaches cleanly.
_INBOX_VIEW_REGISTERED: Final = "inbox_view_registered"


class SocialHomeFederationInboxView(HomeAssistantView):
    """Public inbox at ``/api/social_home/inbox/{inbox_id}``.

    One view serves every config entry — ``inbox_id`` is the unique
    tag a peer was handed at pairing time, and it fully identifies
    which Social Home instance should receive the envelope. We look
    up the owning config entry on every request rather than binding
    the view to a single client, so reloads don't leak stale
    references.
    """

    url = "/api/social_home/inbox/{inbox_id}"
    name = "api:social_home:inbox"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def post(self, request: web.Request, inbox_id: str) -> web.Response:
        # Body size gate — matches spec §24.11 / core's 1 MiB cap.
        # Check ``Content-Length`` first when the peer was polite
        # enough to send one; otherwise fall back to the body after
        # ``read()``.
        content_length = request.content_length
        if content_length is not None and content_length > _MAX_ENVELOPE_BYTES:
            return web.json_response({"error": "envelope_too_large"}, status=413)
        try:
            body = await request.read()
        except web.HTTPException:
            raise
        except Exception as err:  # defensive — aiohttp should have typed this
            _LOGGER.debug("Social Home: inbox body read failed: %s", err)
            return web.json_response({"error": "bad_body"}, status=400)
        if len(body) > _MAX_ENVELOPE_BYTES:
            return web.json_response({"error": "envelope_too_large"}, status=413)

        runtime = _resolve_runtime(self._hass)
        if runtime is None:
            # Inbox received before any config entry finished
            # setup. Signal 503 so the peer retries rather than
            # treating the envelope as permanently rejected.
            return web.json_response({"error": "not_ready"}, status=503)

        # Spec §7.10 mentions an ``X-SocialHome-Signature`` header.
        # The add-on currently pulls the signature from inside the
        # envelope JSON, so the header is redundant — but we
        # forward it when present so spec-compliant peers don't get
        # silently stripped.
        extra_headers: dict[str, str] = {}
        sig = request.headers.get("X-SocialHome-Signature")
        if sig is not None:
            extra_headers["X-SocialHome-Signature"] = sig

        try:
            result = await runtime.client.federation.forward_inbox_envelope(
                inbox_id, body, extra_headers=extra_headers or None
            )
        except SHClientError as err:
            _LOGGER.warning("Social Home: inbox relay for %s failed: %s", inbox_id, err)
            return web.json_response({"error": "bad_gateway"}, status=502)

        return web.Response(
            body=result.body,
            status=result.status,
            content_type=result.content_type,
        )


def async_register_inbox_view(hass: HomeAssistant) -> None:
    """Register :class:`SocialHomeFederationInboxView` exactly once.

    Called from :func:`async_setup_entry`. The flag in
    ``hass.data[DOMAIN]`` keeps a reload — or a second config entry
    in the rare multi-account case — from colliding with the
    existing URL registration.
    """
    bucket = hass.data.setdefault(DOMAIN, {})
    if bucket.get(_INBOX_VIEW_REGISTERED):
        return
    hass.http.register_view(SocialHomeFederationInboxView(hass))
    bucket[_INBOX_VIEW_REGISTERED] = True


def _resolve_runtime(hass: HomeAssistant) -> SocialHomeRuntimeData | None:
    """Return the runtime data of the single active config entry, if any.

    v1 assumes one Social Home account per HA instance. When more
    than one entry exists (e.g. an admin running both a local and
    an external Social Home), we pick the first loaded one — a
    proper ``inbox_id`` → entry lookup table can land in a follow-
    up once the multi-account case has a concrete owner.
    """
    for entry in hass.config_entries.async_entries(DOMAIN):
        runtime = getattr(entry, "runtime_data", None)
        if runtime is not None:
            return runtime  # type: ignore[no-any-return]
    return None
