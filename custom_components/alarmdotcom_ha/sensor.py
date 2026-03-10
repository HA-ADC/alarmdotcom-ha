"""Sensor platform for alarmdotcom_ha — battery, diagnostic, and thermostat sensors."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from pyadc.events import EventBrokerTopic
from pyadc.models.base import AdcDeviceResource
from pyadc.models.thermostat import Thermostat

from .const import DATA_BRIDGE, DOMAIN
from .hub import AlarmHub

log = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities."""
    hub: AlarmHub = hass.data[DOMAIN][entry.entry_id][DATA_BRIDGE]
    entities: list[SensorEntity] = []

    all_devices: list[AdcDeviceResource] = [
        *hub.bridge.partitions.devices,
        *hub.bridge.sensors.devices,
        *hub.bridge.locks.devices,
        *hub.bridge.lights.devices,
        *hub.bridge.garage_doors.devices,
        *hub.bridge.gates.devices,
        *hub.bridge.water_valves.devices,
        *hub.bridge.thermostats.devices,
    ]

    for device in all_devices:
        if device.battery_level_pct is not None:
            entities.append(AdcBatterySensor(hub, device))

    for thermostat in hub.bridge.thermostats.devices:
        entities.append(AdcThermostatTemperatureSensor(hub, thermostat))
        if thermostat.supports_humidity_control:
            entities.append(AdcThermostatHumiditySensor(hub, thermostat))

    async_add_entities(entities)


class _AdcSensorBase(SensorEntity):
    """Base for all alarmdotcom_ha sensor entities."""

    should_poll = False
    _attr_has_entity_name = True

    def __init__(self, hub: AlarmHub, device: AdcDeviceResource) -> None:
        self._hub = hub
        self._device = device
        self._unsubscribe: Any = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.resource_id)},
            name=device.name,
            manufacturer="Alarm.com",
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to resource update events."""
        self._unsubscribe = self._hub.bridge.event_broker.subscribe(
            [EventBrokerTopic.RESOURCE_UPDATED],
            self._handle_update,
            device_id=self._device.resource_id,
        )

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe on removal."""
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    def _handle_update(self, message: object) -> None:
        """Handle state update from EventBroker.

        NOTE: EventBroker callbacks run in HA's event loop (WS processor task),
        so async_write_ha_state() is safe to call directly here.
        """
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Return False when WebSocket is disconnected or device is disabled."""
        return self._hub.connected and not self._device.is_disabled


class AdcBatterySensor(_AdcSensorBase):
    """Battery level sensor for any ADC device that reports battery %."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = True
    _attr_name = "Battery"

    def __init__(self, hub: AlarmHub, device: AdcDeviceResource) -> None:
        super().__init__(hub, device)
        self._attr_unique_id = f"{device.resource_id}_battery"

    @property
    def native_value(self) -> int | None:
        """Return battery level as percentage."""
        if self._device.is_disabled:
            return None
        return self._device.battery_level_pct

    @property
    def available(self) -> bool:
        """Unavailable when WebSocket is disconnected or device is disabled."""
        return self._hub.connected and not self._device.is_disabled


class AdcThermostatTemperatureSensor(_AdcSensorBase):
    """Current temperature sensor for a thermostat."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_name = "Current Temperature"

    def __init__(self, hub: AlarmHub, thermostat: Thermostat) -> None:
        super().__init__(hub, thermostat)
        self._attr_unique_id = f"{thermostat.resource_id}_temperature"
        from homeassistant.const import UnitOfTemperature
        self._attr_native_unit_of_measurement = (
            UnitOfTemperature.CELSIUS
            if thermostat.temperature_unit == "C"
            else UnitOfTemperature.FAHRENHEIT
        )

    @property
    def native_value(self) -> float | None:
        """Return current temperature."""
        return self._device.current_temperature


class AdcThermostatHumiditySensor(_AdcSensorBase):
    """Current humidity sensor for a thermostat that supports it."""

    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_name = "Current Humidity"

    def __init__(self, hub: AlarmHub, thermostat: Thermostat) -> None:
        super().__init__(hub, thermostat)
        self._attr_unique_id = f"{thermostat.resource_id}_humidity"

    @property
    def native_value(self) -> float | None:
        """Return current humidity."""
        return self._device.current_humidity
