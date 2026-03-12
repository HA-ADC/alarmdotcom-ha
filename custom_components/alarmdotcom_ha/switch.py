"""Switch platform for alarmdotcom_ha — on/off light-switch devices."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from pyadc.const import DeviceType, LightState
from pyadc.models.light import Light

from .const import DATA_BRIDGE, DOMAIN
from .entity import AdcEntity
from .hub import AlarmHub

log = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switch entities for ADC light-switch devices (DeviceType 17)."""
    hub: AlarmHub = hass.data[DOMAIN][entry.entry_id][DATA_BRIDGE]
    async_add_entities(
        AdcSwitch(hub, light)
        for light in hub.bridge.lights.devices
        if light.device_type == DeviceType.LIGHT_SWITCH_CONTROL
    )


class AdcSwitch(AdcEntity[Light], SwitchEntity):
    """Alarm.com on/off switch as a HA switch entity."""

    _attr_icon = "mdi:light-switch"

    @property
    def is_on(self) -> bool:
        """Return True when the switch is on."""
        return self._device.state in (LightState.ON, LightState.LEVEL_CHANGE)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        await self._hub.bridge.lights.turn_on(self._device.resource_id)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        await self._hub.bridge.lights.turn_off(self._device.resource_id)
