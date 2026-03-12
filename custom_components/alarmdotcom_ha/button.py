"""Button platform for alarmdotcom_ha — action and diagnostic buttons."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from pyadc.models.base import AdcDeviceResource
from pyadc.models.sensor import Sensor

from .const import DATA_BRIDGE, DOMAIN
from .entity import AdcEntity
from .hub import AlarmHub

log = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up button entities."""
    hub: AlarmHub = hass.data[DOMAIN][entry.entry_id][DATA_BRIDGE]
    entities: list[ButtonEntity] = []

    # Bypass/unbypass buttons for each sensor
    for sensor in hub.bridge.sensors.devices:
        entities.append(AdcSensorBypassButton(hub, sensor))
        entities.append(AdcSensorUnbypassButton(hub, sensor))

    # Debug refresh buttons for every device
    all_devices: list[AdcDeviceResource] = [
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
    ]
    entities.extend(AdcDebugButton(hub, device) for device in all_devices)

    async_add_entities(entities)


class AdcSensorBypassButton(AdcEntity[Sensor], ButtonEntity):
    """Button that bypasses the sensor on its partition."""

    _attr_icon = "mdi:shield-off-outline"
    _attr_has_entity_name = True
    _attr_name = "Bypass"

    def __init__(self, hub: AlarmHub, sensor: Sensor) -> None:
        super().__init__(hub, sensor)
        self._attr_unique_id = f"{sensor.resource_id}_bypass"

    @property
    def available(self) -> bool:
        """Available when connected and sensor is not already bypassed."""
        return self._hub.connected and not self._device.is_disabled and not self._device.bypassed

    async def async_press(self) -> None:
        """Bypass this sensor on the partition."""
        await self._hub.bridge.sensors.bypass(self._device.resource_id)


class AdcSensorUnbypassButton(AdcEntity[Sensor], ButtonEntity):
    """Button that removes a bypass from the sensor."""

    _attr_icon = "mdi:shield-check-outline"
    _attr_has_entity_name = True
    _attr_name = "Remove Bypass"

    def __init__(self, hub: AlarmHub, sensor: Sensor) -> None:
        super().__init__(hub, sensor)
        self._attr_unique_id = f"{sensor.resource_id}_unbypass"

    @property
    def available(self) -> bool:
        """Available when connected and sensor is currently bypassed."""
        return self._hub.connected and not self._device.is_disabled and self._device.bypassed

    async def async_press(self) -> None:
        """Remove the bypass from this sensor."""
        await self._hub.bridge.sensors.unbypass(self._device.resource_id)


class AdcDebugButton(AdcEntity[AdcDeviceResource], ButtonEntity):
    """Diagnostic button that forces a full state refresh from the ADC API.

    Pressing it triggers refresh_all() so the device's current state is
    pulled from REST — useful when you want to confirm or unstick state.
    The logbook records each press as a timestamped event.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:bug"
    _attr_has_entity_name = True
    _attr_name = "Debug"

    def __init__(self, hub: AlarmHub, device: AdcDeviceResource) -> None:
        super().__init__(hub, device)
        self._attr_unique_id = f"{device.resource_id}_debug"

    async def async_press(self) -> None:
        """Force a full state refresh from the ADC REST API."""
        await self._hub.bridge.refresh_all()
