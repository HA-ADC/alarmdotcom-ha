"""Switch platform for alarmdotcom_ha — on/off light-switch devices."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from pyadc.const import LightState
from pyadc.models.light import Light
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
    """Set up switch entities: ADC light-switch devices and sensor bypass toggles."""
    hub: AlarmHub = hass.data[DOMAIN][entry.entry_id][DATA_BRIDGE]
    entities: list[SwitchEntity] = [
        AdcSwitch(hub, light) for light in hub.bridge.lights.devices if light.is_switch
    ]

    # Sensor bypass. Alarm.com bypasses sensors via a *partition* command
    # (POST devices/partitions/{id}/bypassSensors), so we route through the
    # partition. Sensors carry no partition reference, so we use the single
    # partition (the residential norm); with multiple partitions we fall back to
    # the first. Only sensors the panel reports as bypassable get a switch.
    partitions = hub.bridge.partitions.devices
    if partitions:
        partition_id = partitions[0].resource_id
        entities.extend(
            AdcSensorBypassSwitch(hub, sensor, partition_id)
            for sensor in hub.bridge.sensors.devices
            if sensor.supports_bypass
        )
    async_add_entities(entities)


class AdcSwitch(AdcEntity[Light], SwitchEntity):
    """Alarm.com on/off switch as a HA switch entity."""

    @property
    def icon(self) -> str:
        """Return icon reflecting the current switch state."""
        return "mdi:toggle-switch-variant" if self.is_on else "mdi:toggle-switch-variant-off"

    @property
    def is_on(self) -> bool:
        """Return True when the switch is on."""
        return self._device.state in (LightState.ON, LightState.LEVEL_CHANGE)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        self._device.state = LightState.ON
        self.async_write_ha_state()
        await self._hub.bridge.lights.turn_on(self._device.resource_id)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        self._device.state = LightState.OFF
        self.async_write_ha_state()
        await self._hub.bridge.lights.turn_off(self._device.resource_id)


class AdcSensorBypassSwitch(AdcEntity[Sensor], SwitchEntity):
    """Bypass toggle for an Alarm.com sensor.

    ``on`` = bypassed (excluded from arming). Bypass is applied via the owning
    partition's ``bypassSensors`` command; state reflects the live ``bypassed``
    flag from the status bitmask.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_icon = "mdi:shield-off-outline"

    def __init__(self, hub: AlarmHub, device: Sensor, partition_id: str) -> None:
        super().__init__(hub, device)
        self._partition_id = partition_id
        self._attr_unique_id = f"{device.resource_id}_bypass"
        self._attr_name = "Bypass"

    @property
    def is_on(self) -> bool:
        """Return True when the sensor is currently bypassed."""
        return bool(self._device.bypassed)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Bypass the sensor via its partition."""
        self._device.bypassed = True
        self.async_write_ha_state()
        await self._hub.bridge.partitions.bypass_sensors(
            self._partition_id, [self._device.resource_id], bypass=True
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Remove the bypass via its partition."""
        self._device.bypassed = False
        self.async_write_ha_state()
        await self._hub.bridge.partitions.bypass_sensors(
            self._partition_id, [self._device.resource_id], bypass=False
        )
