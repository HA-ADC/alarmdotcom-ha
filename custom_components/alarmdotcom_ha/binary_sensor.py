"""Binary sensor platform for alarmdotcom_ha."""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from pyadc.const import DeviceType
from pyadc.models.base import AdcDeviceResource
from pyadc.models.sensor import Sensor

from .const import DATA_BRIDGE, DOMAIN
from .entity import AdcEntity
from .hub import AlarmHub

log = logging.getLogger(__name__)

# Map DeviceType → BinarySensorDeviceClass
_DEVICE_CLASS_MAP: dict[DeviceType, BinarySensorDeviceClass] = {
    DeviceType.CONTACT: BinarySensorDeviceClass.DOOR,
    DeviceType.CONTACT_MULTI_FUNCTION: BinarySensorDeviceClass.DOOR,
    DeviceType.MOTION: BinarySensorDeviceClass.MOTION,
    DeviceType.IQ_PANEL_MOTION: BinarySensorDeviceClass.MOTION,
    DeviceType.CAMERA: BinarySensorDeviceClass.MOTION,
    DeviceType.CLIMAX_PIR_CAMERA: BinarySensorDeviceClass.MOTION,
    DeviceType.DSC_PIR_CAMERA: BinarySensorDeviceClass.MOTION,
    DeviceType.QOLSYS_PANEL_CAMERA: BinarySensorDeviceClass.MOTION,
    DeviceType.HONEYWELL_PANEL_CAMERA: BinarySensorDeviceClass.MOTION,
    DeviceType.POWERG_PIR_CAMERA: BinarySensorDeviceClass.MOTION,
    DeviceType.GC_NEXT_PANEL_CAMERA: BinarySensorDeviceClass.MOTION,
    DeviceType.SOUND: BinarySensorDeviceClass.SOUND,
    DeviceType.GLASSBREAK: BinarySensorDeviceClass.SOUND,
    DeviceType.IQ_PANEL_GLASSBREAK: BinarySensorDeviceClass.SOUND,
    DeviceType.SMOKE_HEAT: BinarySensorDeviceClass.SMOKE,
    DeviceType.IQ_SMOKE_MULTI_FUNCTION: BinarySensorDeviceClass.SMOKE,
    DeviceType.CARBON_MONOXIDE: BinarySensorDeviceClass.CO,
    DeviceType.WATER: BinarySensorDeviceClass.MOISTURE,
    DeviceType.WATER_FLOOD: BinarySensorDeviceClass.MOISTURE,
    DeviceType.WATER_MULTI_FUNCTION: BinarySensorDeviceClass.MOISTURE,
    DeviceType.TEMPERATURE: BinarySensorDeviceClass.COLD,
    DeviceType.TEMPERATURE_SENSOR: BinarySensorDeviceClass.COLD,
    DeviceType.GAS: BinarySensorDeviceClass.GAS,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensor entities and diagnostic entities for all devices."""
    hub: AlarmHub = hass.data[DOMAIN][entry.entry_id][DATA_BRIDGE]

    entities: list[BinarySensorEntity] = []

    # Main sensor entities
    for sensor in hub.bridge.sensors.devices:
        entities.append(AdcBinarySensor(hub, sensor))

    # Diagnostic entities for every device type
    all_devices: list[AdcDeviceResource] = [
        *hub.bridge.sensors.devices,
        *hub.bridge.locks.devices,
        *hub.bridge.lights.devices,
        *hub.bridge.partitions.devices,
        *hub.bridge.thermostats.devices,
        *hub.bridge.garage_doors.devices,
        *hub.bridge.gates.devices,
        *hub.bridge.water_valves.devices,
        *hub.bridge.water_sensors.devices,
    ]
    for device in all_devices:
        entities.append(AdcMalfunctionSensor(hub, device))
        entities.append(AdcLowBatterySensor(hub, device))

    async_add_entities(entities)


class AdcBinarySensor(AdcEntity[Sensor], BinarySensorEntity):
    """Alarm.com sensor as a HA binary sensor."""

    @property
    def device_class(self) -> BinarySensorDeviceClass | None:
        """Return device class based on the ADC device type."""
        return _DEVICE_CLASS_MAP.get(self._device.device_type)

    @property
    def is_on(self) -> bool:
        """Return True when the sensor is in an active/triggered state."""
        return self._device.is_open


class AdcMalfunctionSensor(AdcEntity[AdcDeviceResource], BinarySensorEntity):
    """Diagnostic binary sensor: True when the device reports a malfunction."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True

    def __init__(self, hub: AlarmHub, device: AdcDeviceResource) -> None:
        super().__init__(hub, device)
        self._attr_unique_id = f"{device.resource_id}_malfunction"
        self._attr_name = "Malfunction"

    @property
    def icon(self) -> str:
        return "mdi:alert-circle" if self.is_on else "mdi:check-circle"

    @property
    def is_on(self) -> bool:
        return self._device.malfunction


class AdcLowBatterySensor(AdcEntity[AdcDeviceResource], BinarySensorEntity):
    """Diagnostic binary sensor: True when the device has a low battery."""

    _attr_device_class = BinarySensorDeviceClass.BATTERY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True

    def __init__(self, hub: AlarmHub, device: AdcDeviceResource) -> None:
        super().__init__(hub, device)
        self._attr_unique_id = f"{device.resource_id}_low_battery"
        self._attr_name = "Low Battery"

    @property
    def is_on(self) -> bool:
        return self._device.low_battery or self._device.critical_battery
