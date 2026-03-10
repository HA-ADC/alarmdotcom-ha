"""Lock platform for alarmdotcom_ha."""

from __future__ import annotations

import logging

from homeassistant.components.lock import LockEntity, LockEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from pyadc.const import LockState
from pyadc.models.lock import Lock

from .const import DATA_BRIDGE, DOMAIN
from .entity import AdcEntity
from .hub import AlarmHub

log = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up lock entities."""
    hub: AlarmHub = hass.data[DOMAIN][entry.entry_id][DATA_BRIDGE]
    async_add_entities(AdcLock(hub, lock) for lock in hub.bridge.locks.devices)


class AdcLock(AdcEntity[Lock], LockEntity):
    """Alarm.com lock as a HA lock entity."""

    @property
    def is_locked(self) -> bool | None:
        """Return True if the lock is locked."""
        if self._device.state == LockState.LOCKED:
            return True
        if self._device.state == LockState.UNLOCKED:
            return False
        return None

    @property
    def is_jammed(self) -> bool:
        """Return True if the lock is jammed."""
        return self._device.state == LockState.UNKNOWN

    async def async_lock(self, **kwargs) -> None:
        """Lock the lock."""
        await self._hub.bridge.locks.lock(self._device.resource_id)

    async def async_unlock(self, **kwargs) -> None:
        """Unlock the lock."""
        await self._hub.bridge.locks.unlock(self._device.resource_id)
