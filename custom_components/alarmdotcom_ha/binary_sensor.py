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
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from pyadc.const import DeviceType
from pyadc.events import EventBrokerTopic
from pyadc.models.base import AdcDeviceResource
from pyadc.models.camera import Camera
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
    # NOTE: Panel cameras (QOLSYS/HONEYWELL/GC_NEXT_PANEL_CAMERA) and PIR image
    # cameras (CLIMAX/DSC/POWERG_PIR_CAMERA) are intentionally NOT mapped to
    # MOTION. They are image-sensor-class devices that emit zero motion events
    # (confirmed via live WebSocket capture + backend categorization as
    # ImageSensors), so a motion binary_sensor never worked. They are exposed as
    # image entities instead (see image.py, _IMAGE_CAMERA_TYPES).
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
    DeviceType.GARAGE_DOOR: BinarySensorDeviceClass.GARAGE_DOOR,
}

# Device types that must NOT get a main binary_sensor entity:
#   * Temperature sensors are real (numeric) sensors, handled by the sensor platform.
#   * Panel cameras / PIR image cameras are image-sensor-class devices with no
#     motion events; they are exposed as image entities (see image.py). They still
#     retain their diagnostic malfunction/low-battery binary sensors below.
_BINARY_SENSOR_EXCLUDED_TYPES = {
    DeviceType.TEMPERATURE,
    DeviceType.TEMPERATURE_SENSOR,
    DeviceType.QOLSYS_PANEL_CAMERA,
    DeviceType.HONEYWELL_PANEL_CAMERA,
    DeviceType.GC_NEXT_PANEL_CAMERA,
    DeviceType.CLIMAX_PIR_CAMERA,
    DeviceType.DSC_PIR_CAMERA,
    DeviceType.POWERG_PIR_CAMERA,
}


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

    # Per-camera object-detection sensors (person / vehicle / animal / package).
    # Real cameras emit video-analytics events that the camera controller
    # decodes into momentary detection flags on the Camera model.
    for camera in hub.bridge.cameras.devices:
        entities.append(AdcCameraPersonSensor(hub, camera))
        entities.append(AdcCameraVehicleSensor(hub, camera))
        entities.append(AdcCameraAnimalSensor(hub, camera))
        entities.append(AdcCameraPackageSensor(hub, camera))

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
        """Return device class based on the ADC device type.

        Alarm.com's customer API models door and window contacts as the same
        physical "door/window contact" device type, so it carries no reliable
        door-vs-window flag — contacts therefore default to ``door``. To have a
        window contact classified as a window, override it per-entity in Home
        Assistant via the entity's Settings → "Show as" selector; that choice is
        stored in the entity registry and takes precedence over this default.
        See GitHub issue #1 and the README.
        """
        return _DEVICE_CLASS_MAP.get(self._device.device_type)

    @property
    def is_on(self) -> bool:
        """Return True when the sensor is in an active/triggered state."""
        return self._device.is_open


class _AdcCameraDetectionSensor(AdcEntity[Camera], BinarySensorEntity):
    """Base for a camera's momentary object-detection binary sensor.

    Cameras support person / vehicle / animal / package detection. Each is
    exposed as a separate momentary binary sensor: the pyadc camera controller sets the
    matching model flag True on a video-analytics event and auto-clears it after
    ~30s. We use device_class MOTION for all three because these are momentary
    "an object was seen moving" pulses (auto-cleared like motion) rather than a
    sustained presence — MOTION renders as Detected/Clear, which fits. (OCCUPANCY
    was considered for person but implies sustained presence, so it was not used.)
    Distinct icons differentiate the three.
    """

    _attr_device_class = BinarySensorDeviceClass.MOTION
    _attr_has_entity_name = True

    # Subclasses set these.
    _detection_attr: str = ""
    _detection_suffix: str = ""

    def __init__(self, hub: AlarmHub, camera: Camera) -> None:
        super().__init__(hub, camera)
        self._attr_unique_id = f"{camera.resource_id}_{self._detection_suffix}"
        # AdcEntity.__init__ sets self._attr_name = None (device-name only), so set
        # a distinct sub-name per detector — "<Camera> Person/Vehicle/Animal".
        # Derive it from the plain _detection_suffix string (NOT the class-level
        # _attr_name, which HA turns into a descriptor).
        self._attr_name = self._detection_suffix.capitalize()

    @property
    def is_on(self) -> bool:
        return bool(getattr(self._device, self._detection_attr, False))


class AdcCameraPersonSensor(_AdcCameraDetectionSensor):
    """Momentary binary sensor: True when the camera detects a person."""

    _attr_icon = "mdi:walk"
    _detection_attr = "person_detected"
    _detection_suffix = "person"


class AdcCameraVehicleSensor(_AdcCameraDetectionSensor):
    """Momentary binary sensor: True when the camera detects a vehicle."""

    _attr_icon = "mdi:car"
    _detection_attr = "vehicle_detected"
    _detection_suffix = "vehicle"


class AdcCameraAnimalSensor(_AdcCameraDetectionSensor):
    """Momentary binary sensor: True when the camera detects an animal."""

    _attr_icon = "mdi:paw"
    _detection_attr = "animal_detected"
    _detection_suffix = "animal"


class AdcCameraPackageSensor(_AdcCameraDetectionSensor):
    """Momentary binary sensor: True when the camera detects a package/parcel."""

    _attr_icon = "mdi:package-variant-closed"
    _detection_attr = "package_detected"
    _detection_suffix = "package"


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


class _AdcWaterMeterBinaryBase(BinarySensorEntity):
    """Base class for water meter binary sensor entities.

    Handles EventBroker subscription for polled water meter updates and
    refreshes the meter reference from the controller on each poll.
    """

    should_poll = False
    _attr_has_entity_name = True

    def __init__(self, hub: AlarmHub, meter: WaterMeter) -> None:
        self._hub = hub
        self._meter = meter
        self._unsubscribe_refresh = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, meter.resource_id)},
            name=meter.name,
            manufacturer="Alarm.com",
            model=meter.model_label,
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to water meter refresh events."""
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


class AdcWaterMeterAnomalySensor(_AdcWaterMeterBinaryBase):
    """Water Dragon anomaly alert binary sensor."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_name = "Water Anomaly"
    _attr_icon = "mdi:water-alert"

    def __init__(self, hub: AlarmHub, meter: WaterMeter) -> None:
        super().__init__(hub, meter)
        self._attr_unique_id = f"{meter.resource_id}_anomaly"

    @property
    def is_on(self) -> bool:
        """Return True when a water anomaly / trouble condition is active."""
        return self._meter.is_leaking


class AdcWaterCalibrationSensor(_AdcWaterMeterBinaryBase):
    """Diagnostic binary sensor: True when the water meter needs calibration."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Needs Calibration"
    _attr_icon = "mdi:tune"

    def __init__(self, hub: AlarmHub, meter: WaterMeter) -> None:
        super().__init__(hub, meter)
        self._attr_unique_id = f"{meter.resource_id}_needs_calibration"

    @property
    def is_on(self) -> bool:
        return self._meter.requires_calibration_setup
