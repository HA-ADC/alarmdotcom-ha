"""Cover platform for alarmdotcom_ha (garage doors and gates).

Garage doors use ``CoverDeviceClass.GARAGE``; gates use
``CoverDeviceClass.GATE``.  Separating the two device classes is intentional:
the community library ``pyalarmdotcomajax`` originally used ``GARAGE`` for
both, which caused incorrect iconography and behaviour in HA for gates
(gates should show as a gate, not a garage door).
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from pyadc.const import CoverState
from pyadc.models.cover import GarageDoor, Gate

from .const import DATA_BRIDGE, DOMAIN
from .entity import AdcEntity
from .hub import AlarmHub

log = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up cover entities for garage doors and gates."""
    hub: AlarmHub = hass.data[DOMAIN][entry.entry_id][DATA_BRIDGE]
    entities: list[CoverEntity] = []
    entities.extend(
        AdcGarageDoor(hub, gd) for gd in hub.bridge.garage_doors.devices
    )
    entities.extend(
        AdcGate(hub, gate) for gate in hub.bridge.gates.devices
    )
    async_add_entities(entities)


class _AdcCoverBase(AdcEntity, CoverEntity):
    """Shared logic for cover devices."""

    _attr_supported_features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE

    @property
    def is_open(self) -> bool | None:
        """Return True if the cover is open."""
        if self._device.state == CoverState.OPEN:
            return True
        if self._device.state == CoverState.CLOSED:
            return False
        return None

    @property
    def is_closed(self) -> bool | None:
        """Return True if the cover is closed."""
        if self._device.state == CoverState.CLOSED:
            return True
        if self._device.state == CoverState.OPEN:
            return False
        return None

    @property
    def is_opening(self) -> bool:
        """Return True if the cover is currently opening."""
        return self._device.state == CoverState.OPENING

    @property
    def is_closing(self) -> bool:
        """Return True if the cover is currently closing."""
        return self._device.state == CoverState.CLOSING


class AdcGarageDoor(_AdcCoverBase):
    """Alarm.com garage door — ``CoverDeviceClass.GARAGE``."""

    _attr_device_class = CoverDeviceClass.GARAGE

    def __init__(self, hub: AlarmHub, garage_door: GarageDoor) -> None:
        super().__init__(hub, garage_door)

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the garage door."""
        self._device.state = CoverState.OPENING
        self.async_write_ha_state()
        await self._hub.bridge.garage_doors.open(self._device.resource_id)

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the garage door."""
        self._device.state = CoverState.CLOSING
        self.async_write_ha_state()
        await self._hub.bridge.garage_doors.close(self._device.resource_id)


class AdcGate(_AdcCoverBase):
    """Alarm.com gate — ``CoverDeviceClass.GATE`` (NOT ``GARAGE``).

    Using the correct GATE device class gives the entity the right icon and
    semantic meaning in HA.  The community library previously (incorrectly)
    used GARAGE for all cover devices, which this integration fixes.
    """

    _attr_device_class = CoverDeviceClass.GATE

    def __init__(self, hub: AlarmHub, gate: Gate) -> None:
        super().__init__(hub, gate)

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the gate."""
        self._device.state = CoverState.OPENING
        self.async_write_ha_state()
        await self._hub.bridge.gates.open(self._device.resource_id)

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the gate."""
        self._device.state = CoverState.CLOSING
        self.async_write_ha_state()
        await self._hub.bridge.gates.close(self._device.resource_id)
