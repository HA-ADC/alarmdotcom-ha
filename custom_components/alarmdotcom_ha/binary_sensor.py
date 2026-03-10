"""Binary sensor platform for alarmdotcom_ha."""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from pyadc.const import DeviceType
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
    """Set up binary sensor entities."""
    hub: AlarmHub = hass.data[DOMAIN][entry.entry_id][DATA_BRIDGE]
    async_add_entities(
        AdcBinarySensor(hub, sensor) for sensor in hub.bridge.sensors.devices
    )


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

    @property
    def extra_state_attributes(self) -> dict:
        """Return diagnostic attributes."""
        return {
            "malfunction": self._device.malfunction,
            "bypassed": self._device.bypassed,
            "tamper": self._device.tamper,
            "low_battery": self._device.low_battery,
            "critical_battery": self._device.critical_battery,
        }
