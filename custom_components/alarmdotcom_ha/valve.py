"""Valve platform for alarmdotcom_ha (water valves)."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.valve import ValveDeviceClass, ValveEntity, ValveEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from pyadc.const import ValveState
from pyadc.models.valve import WaterValve

from .const import DATA_BRIDGE, DOMAIN
from .entity import AdcEntity
from .hub import AlarmHub

log = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up water valve entities."""
    hub: AlarmHub = hass.data[DOMAIN][entry.entry_id][DATA_BRIDGE]
    async_add_entities(
        AdcWaterValve(hub, valve) for valve in hub.bridge.water_valves.devices
    )


class AdcWaterValve(AdcEntity[WaterValve], ValveEntity):
    """Alarm.com water valve as a HA valve entity."""

    _attr_device_class = ValveDeviceClass.WATER
    _attr_supported_features = ValveEntityFeature.OPEN | ValveEntityFeature.CLOSE
    _attr_reports_position = False

    @property
    def is_open(self) -> bool | None:
        """Return True if the valve is open."""
        if self._device.state == ValveState.OPEN:
            return True
        if self._device.state == ValveState.CLOSED:
            return False
        return None

    @property
    def is_closed(self) -> bool | None:
        """Return True if the valve is closed."""
        if self._device.state == ValveState.CLOSED:
            return True
        if self._device.state == ValveState.OPEN:
            return False
        return None

    async def async_open_valve(self, **kwargs: Any) -> None:
        """Open the water valve."""
        await self._hub.bridge.water_valves.open(self._device.resource_id)

    async def async_close_valve(self, **kwargs: Any) -> None:
        """Close the water valve."""
        await self._hub.bridge.water_valves.close(self._device.resource_id)
