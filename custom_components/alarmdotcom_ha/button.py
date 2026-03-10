"""Button platform for alarmdotcom_ha — diagnostic debug buttons."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from pyadc.models.base import AdcDeviceResource

from .const import DATA_BRIDGE, DOMAIN
from .entity import AdcEntity
from .hub import AlarmHub

log = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up debug button entities for every device."""
    hub: AlarmHub = hass.data[DOMAIN][entry.entry_id][DATA_BRIDGE]

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

    async_add_entities(AdcDebugButton(hub, device) for device in all_devices)


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
        self._attr_name = "Debug"

    async def async_press(self) -> None:
        """Force a full state refresh from the ADC REST API."""
        await self._hub.bridge.refresh_all()
