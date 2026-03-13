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
from pyadc.models.water_meter import WaterMeter
from pyadc.models.water_sensor import WaterSensor

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

# Temperature sensors are real sensors (not binary) — exclude from binary sensor platform
_BINARY_SENSOR_EXCLUDED_TYPES = {DeviceType.TEMPERATURE, DeviceType.TEMPERATURE_SENSOR}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensor entities and diagnostic entities for all devices."""
    hub: AlarmHub = hass.data[DOMAIN][entry.entry_id][DATA_BRIDGE]

    entities: list[BinarySensorEntity] = []

    # Main sensor entities (exclude temperature sensors — they get real sensor entities)
    for sensor in hub.bridge.sensors.devices:
        if sensor.device_type not in _BINARY_SENSOR_EXCLUDED_TYPES:
            entities.append(AdcBinarySensor(hub, sensor))

    # Water sensor moisture state entities
    for water_sensor in hub.bridge.water_sensors.devices:
        entities.append(AdcWaterMoistureSensor(hub, water_sensor))

    # Water Dragon meter anomaly alert entities + calibration diagnostic
    for meter in hub.bridge.water_meters.devices:
        entities.append(AdcWaterMeterAnomalySensor(hub, meter))
        if meter.requires_calibration_setup:
            entities.append(AdcWaterCalibrationSensor(hub, meter))

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


class AdcWaterMoistureSensor(AdcEntity[WaterSensor], BinarySensorEntity):
    """Water/leak sensor as a HA moisture binary sensor."""

    _attr_device_class = BinarySensorDeviceClass.MOISTURE

    @property
    def is_on(self) -> bool:
        """Return True when water/moisture is detected."""
        return self._device.is_wet


class AdcWaterMeterAnomalySensor(BinarySensorEntity):
    """Water Dragon anomaly alert binary sensor.

    True when the most recent daily data point has a trouble condition
    (``htc=true`` in the XML) or the device is reporting a leak state.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_has_entity_name = True
    _attr_name = "Water Anomaly"
    _attr_icon = "mdi:water-alert"
    should_poll = False

    def __init__(self, hub: AlarmHub, meter: WaterMeter) -> None:
        self._hub = hub
        self._meter = meter
        self._unsubscribe_refresh = None
        self._attr_unique_id = f"{meter.resource_id}_anomaly"
        from homeassistant.helpers.entity import DeviceInfo
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, meter.resource_id)},
            name=meter.name,
            manufacturer="Alarm.com",
            model="ADC-SHM-100-A",
        )

    async def async_added_to_hass(self) -> None:
        from pyadc.events import EventBrokerTopic
        self._unsubscribe_refresh = self._hub.bridge.event_broker.subscribe(
            [EventBrokerTopic.RESOURCE_UPDATED],
            self._handle_refresh,
            device_id=self._meter.resource_id,
        )

    async def async_will_remove_from_hass(self) -> None:
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

    @property
    def is_on(self) -> bool:
        """Return True when a water anomaly / trouble condition is active."""
        return self._meter.is_leaking


class AdcWaterCalibrationSensor(BinarySensorEntity):
    """Diagnostic binary sensor: True when the water meter needs calibration.

    The ADC API sets ``requiresCalibrationSetup`` when the device hasn't been
    calibrated yet and its flow readings cannot be trusted.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_name = "Needs Calibration"
    _attr_icon = "mdi:tune"
    should_poll = False

    def __init__(self, hub: AlarmHub, meter: WaterMeter) -> None:
        self._hub = hub
        self._meter = meter
        self._unsubscribe_refresh = None
        self._attr_unique_id = f"{meter.resource_id}_needs_calibration"
        from homeassistant.helpers.entity import DeviceInfo
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, meter.resource_id)},
            name=meter.name,
            manufacturer="Alarm.com",
            model="ADC-SHM-100-A",
        )

    async def async_added_to_hass(self) -> None:
        from pyadc.events import EventBrokerTopic
        self._unsubscribe_refresh = self._hub.bridge.event_broker.subscribe(
            [EventBrokerTopic.RESOURCE_UPDATED],
            self._handle_refresh,
            device_id=self._meter.resource_id,
        )

    async def async_will_remove_from_hass(self) -> None:
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
        return self._hub.bridge.water_meters.get(self._meter.resource_id) is not None

    @property
    def is_on(self) -> bool:
        return self._meter.requires_calibration_setup
