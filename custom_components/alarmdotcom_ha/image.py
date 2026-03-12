"""Image platform for alarmdotcom_ha — image sensors (snapshot cameras)."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from pyadc.models.image_sensor import ImageSensor

from .const import DATA_BRIDGE, DOMAIN
from .entity import AdcEntity
from .hub import AlarmHub

log = logging.getLogger(__name__)

# Image sensors poll to refresh image URL; state changes still arrive via WebSocket.
SCAN_INTERVAL = timedelta(minutes=30)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up image sensor entities."""
    hub: AlarmHub = hass.data[DOMAIN][entry.entry_id][DATA_BRIDGE]
    async_add_entities(
        AdcImageSensor(hub, sensor)
        for sensor in hub.bridge.image_sensors.devices
    )


class AdcImageSensor(AdcEntity[ImageSensor], ImageEntity):
    """Alarm.com image sensor (snapshot on motion/doorbell events).

    State changes (new image available) are pushed via WebSocket
    (ImageSensorUpload event) which triggers async_write_ha_state().
    SCAN_INTERVAL polling is used only to refresh the image URL itself
    via a lightweight REST fetch.
    """

    # Image sensors DO poll for image content — state is still WS-pushed.
    _attr_should_poll = True

    def __init__(self, hub: AlarmHub, device: ImageSensor) -> None:
        AdcEntity.__init__(self, hub, device)
        ImageEntity.__init__(self, None)

    @property
    def image_url(self) -> str | None:
        """Return URL of the most recent image."""
        return self._device.last_image_url

    @property
    def image_last_updated(self):
        """Return timestamp of the most recent image."""
        return self._device.last_update

    async def async_update(self) -> None:
        """Poll for the latest image (re-fetches image sensor data from REST)."""
        await self._hub.bridge.image_sensors.fetch_all()
