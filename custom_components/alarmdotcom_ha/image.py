"""Image platform for alarmdotcom_ha — image sensors (snapshot cameras)."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from pyadc.const import DeviceType
from pyadc.models.image_sensor import ImageSensor
from pyadc.models.sensor import Sensor

from .const import DATA_BRIDGE, DOMAIN
from .entity import AdcEntity
from .hub import AlarmHub

log = logging.getLogger(__name__)

# Image sensors poll to refresh image URL; state changes still arrive via WebSocket.
SCAN_INTERVAL = timedelta(minutes=30)

# Panel cameras and PIR image cameras are image-sensor-class devices: the ADC
# backend categorizes them as ImageSensors and they upload captures through the
# image-sensor upload flow (retrievable via imageSensor/imageSensorImages).
# They arrive in pyadc through the *sensors* endpoint (as Sensor models), so we
# surface them here as image entities rather than (broken, event-less) motion
# binary sensors. See binary_sensor.py for the diagnostic entities they keep.
_IMAGE_CAMERA_TYPES: frozenset[DeviceType] = frozenset(
    {
        DeviceType.QOLSYS_PANEL_CAMERA,
        DeviceType.HONEYWELL_PANEL_CAMERA,
        DeviceType.GC_NEXT_PANEL_CAMERA,
        DeviceType.CLIMAX_PIR_CAMERA,
        DeviceType.DSC_PIR_CAMERA,
        DeviceType.POWERG_PIR_CAMERA,
    }
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up image sensor entities."""
    hub: AlarmHub = hass.data[DOMAIN][entry.entry_id][DATA_BRIDGE]
    # Prime the recent-images cache so panel-camera image entities render the
    # latest capture immediately after startup instead of showing "unknown"
    # until the first SCAN_INTERVAL poll.
    await hub.bridge.image_sensors.fetch_recent_images()
    entities: list[ImageEntity] = [
        AdcImageSensor(hass, hub, sensor)
        for sensor in hub.bridge.image_sensors.devices
    ]
    # Panel / PIR image cameras (surfaced by pyadc as Sensor devices).
    entities.extend(
        AdcPanelCameraImage(hass, hub, device)
        for device in hub.bridge.sensors.devices
        if device.device_type in _IMAGE_CAMERA_TYPES
    )
    async_add_entities(entities)


class AdcImageSensor(AdcEntity[ImageSensor], ImageEntity):
    """Alarm.com image sensor (snapshot on motion/doorbell events).

    State changes (new image available) are pushed via WebSocket
    (ImageSensorUpload event) which triggers async_write_ha_state().
    SCAN_INTERVAL polling is used only to refresh the image URL itself
    via a lightweight REST fetch.
    """

    # Image sensors DO poll for image content — state is still WS-pushed.
    _attr_should_poll = True

    def __init__(self, hass: HomeAssistant, hub: AlarmHub, device: ImageSensor) -> None:
        AdcEntity.__init__(self, hub, device)
        ImageEntity.__init__(self, hass)

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


class AdcPanelCameraImage(AdcEntity[Sensor], ImageEntity):
    """Alarm.com panel / PIR image camera exposed as a HA image entity.

    These devices are delivered by pyadc through the sensors endpoint (as
    :class:`~pyadc.models.sensor.Sensor` models) but are image-sensor-class
    devices that upload captures. The latest capture URL is retrieved from the
    image-sensor "recent images" endpoint via the ImageSensorController and
    keyed by the numeric device id (the short suffix of the resource id).
    """

    # Poll to refresh the latest-image URL, mirroring image sensors.
    _attr_should_poll = True

    def __init__(self, hass: HomeAssistant, hub: AlarmHub, device: Sensor) -> None:
        AdcEntity.__init__(self, hub, device)
        ImageEntity.__init__(self, hass)

    @property
    def _device_short_id(self) -> str:
        """Numeric device id used as the image-sensor image relationship key."""
        return self._device.resource_id.rsplit("-", 1)[-1]

    @property
    def image_last_updated(self):
        """Return timestamp of the most recent capture."""
        return self._hub.bridge.image_sensors.latest_image_timestamp(
            self._device_short_id
        )

    async def async_image(self) -> bytes | None:
        """Fetch the latest capture bytes through the authenticated ADC client.

        The recent-images endpoint returns a relative, session-authenticated URL
        (e.g. ``/web/History/ImageViewer.ashx?...``), which HA's default image
        fetch can't render (no base URL, no ADC cookies). Resolve it to an
        absolute URL and fetch via the pyadc client, which carries the session.
        """
        url = self._hub.bridge.image_sensors.latest_image_url(self._device_short_id)
        if not url:
            return None
        if not url.startswith("http"):
            url = f"{self._hub.bridge.client.base_url}{url}"
        try:
            return await self._hub.bridge.client.fetch_bytes(url)
        except Exception as exc:  # noqa: BLE001 — a failed image fetch is non-fatal
            log.debug("Panel camera %s image fetch failed: %s", self._device.resource_id, exc)
            return None

    async def async_update(self) -> None:
        """Poll the image-sensor recent-images endpoint for the latest capture."""
        await self._hub.bridge.image_sensors.fetch_recent_images()
