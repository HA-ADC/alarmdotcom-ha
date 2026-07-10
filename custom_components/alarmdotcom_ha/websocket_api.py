"""Home Assistant WebSocket API commands for the ADC WebRTC custom card.

The bundled card (``adc-webrtc-card.js``) speaks the Janus protocol directly
from the browser to ADC's gateway — the same flow as ADC's own web player —
so live view works without ``aiortc`` (which cannot be installed on HA OS:
it pins ``av<17`` while HA core ships ``av>=17``). This command hands the
card fresh, short-lived stream credentials; no signaling or media passes
through Home Assistant.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .const import DATA_BRIDGE, DOMAIN

log = logging.getLogger(__name__)


@callback
def async_register(hass: HomeAssistant) -> None:
    """Register the WebSocket API commands (idempotent per HA instance)."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get("_ws_api_registered"):
        return
    domain_data["_ws_api_registered"] = True
    websocket_api.async_register_command(hass, ws_camera_stream_info)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/camera_stream_info",
        vol.Required("entity_id"): str,
        vol.Optional("hd", default=True): bool,
    }
)
@websocket_api.async_response
async def ws_camera_stream_info(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return fresh Janus stream credentials for a camera entity.

    The liveVideoSource credentials expire after ~1 hour, so the card calls
    this on every connect (and on relay fallback with ``hd`` flipped).
    """
    entity_id: str = msg["entity_id"]
    reg_entry = er.async_get(hass).async_get(entity_id)
    if reg_entry is None or reg_entry.platform != DOMAIN:
        connection.send_error(
            msg["id"], "entity_not_found", f"{entity_id} is not an {DOMAIN} entity"
        )
        return

    entry_data = hass.data.get(DOMAIN, {}).get(reg_entry.config_entry_id)
    if not entry_data:
        connection.send_error(msg["id"], "not_loaded", "Integration not loaded")
        return
    hub = entry_data[DATA_BRIDGE]

    # Camera entity unique_id is "{resource_id}_camera" (see camera.py).
    resource_id = reg_entry.unique_id.removesuffix("_camera")
    camera = next(
        (c for c in hub.bridge.cameras.devices if c.resource_id == resource_id),
        None,
    )
    if camera is None:
        connection.send_error(
            msg["id"], "entity_not_found", f"No ADC camera for {entity_id}"
        )
        return

    use_hd: bool = msg["hd"]
    source = await hub.bridge.cameras.get_live_video_source(camera, hd=use_hd)
    if not source or not source.janus_gateway_url or not source.janus_token:
        connection.send_error(
            msg["id"], "stream_unavailable", "Camera stream credentials unavailable"
        )
        return
    if not source.proxy_url:
        connection.send_error(
            msg["id"], "stream_unavailable", "Camera stream proxy URL unavailable"
        )
        return

    ice_servers: list[dict[str, Any]] = []
    if source.ice_servers:
        try:
            ice_servers = json.loads(source.ice_servers)
        except (TypeError, ValueError) as exc:
            log.debug("Camera %s: unparseable ICE servers: %s", resource_id, exc)

    connection.send_result(
        msg["id"],
        {
            "gateway_url": source.janus_gateway_url,
            "token": source.janus_token,
            "media_uri": source.proxy_url,
            # HD relays are continuously-running streams a viewer joins
            # mid-GOP, so SPS/PPS injection is always needed; SD relays start
            # fresh per viewer, where the API's spsAndPpsRequired is accurate
            # (forcing injection on can stall the SD ingest — see camera.py).
            "add_sps_pps": True if use_hd else bool(source.sps_and_pps_required),
            "mountpoint_name": camera.mac_address,
            "ice_servers": ice_servers,
            "hd": use_hd,
        },
    )
