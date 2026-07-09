"""Button platform for alarmdotcom_ha — action and diagnostic buttons."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from pyadc.const import DeviceType
from pyadc.events import ResourceEventMessage
from pyadc.models.base import AdcDeviceResource
from pyadc.models.image_sensor import ImageSensor

from .const import DATA_BRIDGE, DOMAIN
from .entity import AdcEntity
from .hub import AlarmHub

log = logging.getLogger(__name__)

# After a peek-in request, poll for the uploaded frame so the image entity
# refreshes as soon as it lands (observed ~4 s on panel cameras; battery PIR
# cams can take longer) instead of waiting for the image platform's 30-minute
# SCAN_INTERVAL poll.
_PEEK_IN_POLL_INTERVAL_S = 5
_PEEK_IN_POLL_ATTEMPTS = 12  # give up after ~60 s

# Panel / PIR image cameras support on-demand "peek-in" captures. They arrive in
# pyadc through the sensors endpoint (as Sensor models), so peek-in buttons must
# also be created from hub.bridge.sensors — not just the image_sensors collection.
_PEEK_IN_CAMERA_TYPES: frozenset[DeviceType] = frozenset(
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
    """Set up button entities."""
    hub: AlarmHub = hass.data[DOMAIN][entry.entry_id][DATA_BRIDGE]
    entities: list[ButtonEntity] = []

    # Debug refresh buttons for every device. De-duplicate by resource_id: a
    # device can appear in more than one controller collection, which would
    # otherwise create two buttons with the same ``{id}_debug`` unique_id and
    # trigger a "does not generate unique IDs" error.
    all_devices: dict[str, AdcDeviceResource] = {}
    for device in (
        *hub.bridge.cameras.devices,
        *hub.bridge.partitions.devices,
        *hub.bridge.sensors.devices,
        *hub.bridge.locks.devices,
        *hub.bridge.lights.devices,
        *hub.bridge.garage_doors.devices,
        *hub.bridge.gates.devices,
        *hub.bridge.water_valves.devices,
        *hub.bridge.water_sensors.devices,
        *hub.bridge.thermostats.devices,
    ):
        all_devices.setdefault(device.resource_id, device)
    entities.extend(AdcDebugButton(hub, device) for device in all_devices.values())

    # Clear panel faults — one per partition (uses PartitionController.clear_panel_faults)
    entities.extend(AdcClearFaultsButton(hub, p) for p in hub.bridge.partitions.devices)

    # Peek-in now — for image sensors AND panel/PIR image cameras (the latter
    # arrive via the sensors collection). Uses ImageSensorController.peek_in_now.
    entities.extend(AdcPeekInButton(hub, s) for s in hub.bridge.image_sensors.devices)
    entities.extend(
        AdcPeekInButton(hub, s)
        for s in hub.bridge.sensors.devices
        if s.device_type in _PEEK_IN_CAMERA_TYPES
    )

    async_add_entities(entities)


class AdcDebugButton(AdcEntity[AdcDeviceResource], ButtonEntity):
    """Diagnostic button that forces a full state refresh from the ADC API.

    Pressing it triggers refresh_all() so the device's current state is
    pulled from REST — useful when you want to confirm or unstick state.
    The logbook records each press as a timestamped event.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:bug"
    _attr_has_entity_name = True

    def __init__(self, hub: AlarmHub, device: AdcDeviceResource) -> None:
        super().__init__(hub, device)
        self._attr_unique_id = f"{device.resource_id}_debug"
        # Set in __init__: AdcEntity.__init__ resets self._attr_name to None.
        self._attr_name = "Debug"

    async def async_press(self) -> None:
        """Force a full state refresh from the ADC REST API."""
        await self._hub.bridge.refresh_all()


class AdcClearFaultsButton(AdcEntity[AdcDeviceResource], ButtonEntity):
    """Clear outstanding panel faults on a partition."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:alert-circle-check-outline"

    def __init__(self, hub: AlarmHub, device: AdcDeviceResource) -> None:
        super().__init__(hub, device)
        self._attr_unique_id = f"{device.resource_id}_clear_faults"
        self._attr_name = "Clear Faults"

    async def async_press(self) -> None:
        """Send the clear-faults action to the partition."""
        await self._hub.bridge.partitions.clear_panel_faults(self._device.resource_id)


class AdcPeekInButton(AdcEntity[AdcDeviceResource], ButtonEntity):
    """Trigger an on-demand 'peek-in' image capture on an image sensor."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:camera-iris"

    def __init__(self, hub: AlarmHub, device: AdcDeviceResource) -> None:
        super().__init__(hub, device)
        self._attr_unique_id = f"{device.resource_id}_peek_in"
        self._attr_name = "Peek In"
        self._refresh_task: asyncio.Task | None = None

    async def async_press(self) -> None:
        """Request a fresh peek-in capture and refresh the image when it lands."""
        await self._hub.bridge.image_sensors.peek_in_now(self._device.resource_id)
        if self._refresh_task is not None and not self._refresh_task.done():
            self._refresh_task.cancel()
        self._refresh_task = self.hass.async_create_task(
            self._refresh_when_image_lands()
        )

    def _latest_capture(self) -> tuple[object, object]:
        """Return the (timestamp, url) of the device's newest known capture.

        Standalone image sensors carry these on the model itself; panel / PIR
        image cameras (Sensor models) read them from the controller's
        recent-images cache, keyed by the numeric short id.
        """
        if isinstance(self._device, ImageSensor):
            return (self._device.last_update, self._device.last_image_url)
        ctrl = self._hub.bridge.image_sensors
        short_id = self._device.resource_id.rsplit("-", 1)[-1]
        return (
            ctrl.latest_image_timestamp(short_id),
            ctrl.latest_image_url(short_id),
        )

    async def _refresh_when_image_lands(self) -> None:
        """Poll until the peek-in frame uploads, then nudge the image entity."""
        ctrl = self._hub.bridge.image_sensors
        baseline = self._latest_capture()
        for _ in range(_PEEK_IN_POLL_ATTEMPTS):
            await asyncio.sleep(_PEEK_IN_POLL_INTERVAL_S)
            try:
                if isinstance(self._device, ImageSensor):
                    await ctrl.fetch_all()
                else:
                    await ctrl.fetch_recent_images()
            except Exception as exc:  # noqa: BLE001 — keep polling through blips
                log.debug(
                    "Peek-in refresh poll failed for %s: %s",
                    self._device.resource_id, exc,
                )
                continue
            if self._latest_capture() != baseline:
                self._hub.bridge.event_broker.publish(
                    ResourceEventMessage(
                        device_id=self._device.resource_id,
                        device_type=self._device.resource_type,
                    )
                )
                return
        log.debug(
            "Peek-in image for %s did not arrive within %s s",
            self._device.resource_id,
            _PEEK_IN_POLL_INTERVAL_S * _PEEK_IN_POLL_ATTEMPTS,
        )
