"""Camera platform for alarmdotcom_ha.

Each Alarm.com camera is exposed as a HA ``CameraEntity``.

**Still images** are served via ``video/snapshots/{id}`` — a signed HTTPS URL
returned by the ADC relay that contains a JPEG frame.

**Live streaming** requires the ADC Janus WebRTC proxy.  The Janus
``janusGatewayUrl`` + ``janusToken`` + ``iceServers`` are fetched from
``video/videoSources/liveVideoSources/{id}`` and logged for diagnostic
purposes; full WebRTC bridging (via ``aiortc``) is a planned enhancement.
"""

from __future__ import annotations

import logging

from homeassistant.components.camera import Camera as HaCamera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from pyadc.models.camera import Camera

from .const import DATA_BRIDGE, DOMAIN
from .entity import AdcEntity
from .hub import AlarmHub

log = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Alarm.com camera entities."""
    hub: AlarmHub = hass.data[DOMAIN][entry.entry_id][DATA_BRIDGE]
    async_add_entities(
        AdcCamera(hub, camera) for camera in hub.bridge.cameras.devices
    )


class AdcCamera(AdcEntity[Camera], HaCamera):
    """An Alarm.com camera entity.

    Still images are served via the ADC relay snapshot endpoint.
    Live streaming via the Janus WebRTC proxy is a future enhancement.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:cctv"

    def __init__(self, hub: AlarmHub, device: Camera) -> None:
        AdcEntity.__init__(self, hub, device)
        HaCamera.__init__(self)
        self._attr_unique_id = f"{device.resource_id}_camera"
        self._attr_name = None  # entity name = device name

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a JPEG snapshot fetched from the ADC relay."""
        url = await self._hub.bridge.cameras.get_snapshot_url(self._device)
        if not url:
            return None
        try:
            async with self._hub.bridge.client._session.get(
                url,
                headers=self._hub.bridge.client._build_headers(),
                timeout=15,
            ) as resp:
                if resp.status != 200:
                    log.debug(
                        "Camera %s snapshot HTTP %s", self._device.resource_id, resp.status
                    )
                    return None
                return await resp.read()
        except Exception as exc:
            log.debug("Camera %s snapshot error: %s", self._device.resource_id, exc)
            return None

    async def stream_source(self) -> str | None:
        """Return None — live stream requires WebRTC (future enhancement)."""
        return None

