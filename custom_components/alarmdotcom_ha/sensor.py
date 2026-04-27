"""Sensor platform for alarmdotcom_ha — battery, diagnostic, thermostat, and water meter sensors."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTemperature, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from pyadc.events import EventBrokerTopic
from pyadc.models.base import AdcDeviceResource
from pyadc.models.thermostat import Thermostat
from pyadc.models.water_meter import WaterMeter
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
        *hub.bridge.water_sensors.devices,
        *hub.bridge.thermostats.devices,
    ]

    for device in all_devices:
        if device.battery_level_pct is not None:
            entities.append(AdcBatterySensor(hub, device))

    for thermostat in hub.bridge.thermostats.devices:
        entities.append(AdcThermostatTemperatureSensor(hub, thermostat))
        if thermostat.supports_humidity_control:
            entities.append(AdcThermostatHumiditySensor(hub, thermostat))

    for sensor in hub.bridge.sensors.devices:
        if sensor.is_temperature_sensor:
            entities.append(AdcTemperatureSensor(hub, sensor))

    for meter in hub.bridge.water_meters.devices:
        entities.append(AdcWaterUsageTodaySensor(hub, meter))
        entities.append(AdcWaterDailyAvgSensor(hub, meter))

    async_add_entities(entities)


class _AdcSensorBase(AdcEntity[AdcDeviceResource], SensorEntity):
    """Base for alarmdotcom_ha sensor entities backed by AdcEntity."""

    pass


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


class AdcThermostatTemperatureSensor(_AdcSensorBase):
    """Current temperature sensor for a thermostat."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_name = "Current Temperature"

    def __init__(self, hub: AlarmHub, thermostat: Thermostat) -> None:
        super().__init__(hub, thermostat)
        self._attr_unique_id = f"{thermostat.resource_id}_temperature"
        self._attr_native_unit_of_measurement = (
            UnitOfTemperature.CELSIUS
            if thermostat.temperature_unit == "C"
            else UnitOfTemperature.FAHRENHEIT
        )

    @property
    def native_value(self) -> float | None:
        """Return current temperature."""
        return self._device.current_temperature


class AdcTemperatureSensor(_AdcSensorBase):
    """Standalone temperature sensor (PowerG, Z-wave, etc.)

    Initial value comes from commercialTemperatureSensors REST on startup
    (already in account's preferred unit). Live updates come via
    PropertyChangeWSMessage (always °F). HA auto-converts between units.
    """

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_name = "Temperature"

    def __init__(self, hub: AlarmHub, sensor: Sensor) -> None:
        super().__init__(hub, sensor)
        self._attr_unique_id = f"{sensor.resource_id}_temperature"

    @property
    def native_unit_of_measurement(self) -> str:
        return (
            UnitOfTemperature.CELSIUS
            if self._device.temperature_unit == "C"
            else UnitOfTemperature.FAHRENHEIT
        )

    @property
    def native_value(self) -> float | None:
        return self._device.temperature


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


# ---------------------------------------------------------------------------
# Water Dragon (ADC-SHM-100-A) water meter sensors
# ---------------------------------------------------------------------------

class _AdcWaterMeterEntity(SensorEntity):
    """Base class for water meter sensor entities.

    Water meters are polled (no WS events), so these entities subscribe to
    RESOURCE_UPDATED and refresh the meter reference from the controller on
    each poll cycle.
    """

    should_poll = False
    _attr_has_entity_name = True

    def __init__(self, hub: AlarmHub, meter: WaterMeter) -> None:
        self._hub = hub
        self._meter = meter
        self._unsubscribe_refresh: Any = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, meter.resource_id)},
            name=meter.name,
            manufacturer="Alarm.com",
            model=meter.model_label,
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to water meter refresh events from the hub."""
        self._unsubscribe_refresh = self._hub.bridge.event_broker.subscribe(
            [EventBrokerTopic.RESOURCE_UPDATED],
            self._handle_refresh,
            device_id=self._meter.resource_id,
        )

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe on removal."""
        if self._unsubscribe_refresh is not None:
            self._unsubscribe_refresh()
            self._unsubscribe_refresh = None

    def _handle_refresh(self, _message: object) -> None:
        refreshed = self._hub.bridge.water_meters.get(self._meter.resource_id)
        if refreshed is not None:
            self._meter = refreshed
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Water meter entities are polled — available as long as data was fetched."""
        return self._hub.bridge.water_meters.get(self._meter.resource_id) is not None


class AdcWaterUsageTodaySensor(_AdcWaterMeterEntity):
    """Water usage today for a Water Dragon device."""

    _attr_device_class = SensorDeviceClass.WATER
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_name = "Water Usage Today"
    _attr_icon = "mdi:water"

    def __init__(self, hub: AlarmHub, meter: WaterMeter) -> None:
        super().__init__(hub, meter)
        self._attr_unique_id = f"{meter.resource_id}_usage_today"
        self._attr_native_unit_of_measurement = (
            UnitOfVolume.GALLONS if meter.volume_unit == 0 else UnitOfVolume.LITERS
        )

    @property
    def native_value(self) -> float | None:
        return self._meter.usage_today

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose ADC gauge display range so dashboard gauge cards work out of the box."""
        return {
            "daily_display_min": self._meter.daily_usage_display_minimum,
            "daily_display_max": self._meter.daily_usage_display_maximum,
        }


class AdcWaterDailyAvgSensor(_AdcWaterMeterEntity):
    """30-day average daily water usage for a Water Dragon device."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_name = "Water Daily Average"
    _attr_icon = "mdi:water-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hub: AlarmHub, meter: WaterMeter) -> None:
        super().__init__(hub, meter)
        self._attr_unique_id = f"{meter.resource_id}_daily_avg"
        self._attr_native_unit_of_measurement = (
            UnitOfVolume.GALLONS if meter.volume_unit == 0 else UnitOfVolume.LITERS
        )

    @property
    def native_value(self) -> float | None:
        return self._meter.average_daily_usage
