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
    """Alarm.com lock as a HA lock entity.

    Optimistic transitional states: when a lock/unlock command is sent we
    immediately show is_locking/is_unlocking so the UI reflects the in-flight
    operation.  The WS confirmation (MonitorEventWSMessage) triggers
    _handle_update which clears the transition and shows the final state.
    """

    def __init__(self, hub: AlarmHub, device: Lock) -> None:
        super().__init__(hub, device)
        self._is_locking: bool = False
        self._is_unlocking: bool = False

    @property
    def is_locked(self) -> bool | None:
        if self._is_locking or self._is_unlocking:
            return None  # transitioning — don't report locked/unlocked yet
        if self._device.state == LockState.LOCKED:
            return True
        if self._device.state == LockState.UNLOCKED:
            return False
        return None

    @property
    def is_locking(self) -> bool:
        return self._is_locking

    @property
    def is_unlocking(self) -> bool:
        return self._is_unlocking

    @property
    def is_jammed(self) -> bool:
        return self._device.state == LockState.UNKNOWN

    def _handle_update(self, message: object) -> None:
        """WS confirmation arrived — clear transition flags then refresh."""
        self._is_locking = False
        self._is_unlocking = False
        super()._handle_update(message)

    async def async_lock(self, **kwargs) -> None:
        """Send lock command and optimistically show locking state."""
        self._is_locking = True
        self._is_unlocking = False
        self.async_write_ha_state()
        await self._hub.bridge.locks.lock(self._device.resource_id)

    async def async_unlock(self, **kwargs) -> None:
        """Send unlock command and optimistically show unlocking state."""
        self._is_unlocking = True
        self._is_locking = False
        self.async_write_ha_state()
        await self._hub.bridge.locks.unlock(self._device.resource_id)
