"""Alarm control panel platform for alarmdotcom_ha."""

from __future__ import annotations

import logging

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from pyadc.const import ArmingState
from pyadc.models.partition import Partition

from .const import DATA_BRIDGE, DOMAIN
from .entity import AdcEntity
from .hub import AlarmHub

log = logging.getLogger(__name__)

_ARMING_STATE_MAP: dict[ArmingState, AlarmControlPanelState] = {
    ArmingState.DISARMED: AlarmControlPanelState.DISARMED,
    ArmingState.ARMED_STAY: AlarmControlPanelState.ARMED_HOME,
    ArmingState.ARMED_AWAY: AlarmControlPanelState.ARMED_AWAY,
    ArmingState.ARMED_NIGHT: AlarmControlPanelState.ARMED_NIGHT,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up alarm control panel entities."""
    hub: AlarmHub = hass.data[DOMAIN][entry.entry_id][DATA_BRIDGE]
    async_add_entities(
        AdcAlarmControlPanel(hub, partition)
        for partition in hub.bridge.partitions.devices
    )


class AdcAlarmControlPanel(AdcEntity[Partition], AlarmControlPanelEntity):
    """Alarm.com partition as a HA alarm control panel."""

    _attr_code_arm_required = False

    def __init__(self, hub: AlarmHub, partition: Partition) -> None:
        super().__init__(hub, partition)
        self._update_supported_features()

    def _update_supported_features(self) -> None:
        features = (
            AlarmControlPanelEntityFeature.ARM_HOME
            | AlarmControlPanelEntityFeature.ARM_AWAY
        )
        if self._device.supports_night_arming:
            features |= AlarmControlPanelEntityFeature.ARM_NIGHT
        self._attr_supported_features = features

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        """Return the current alarm state."""
        return _ARMING_STATE_MAP.get(self._device.state)

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        """Disarm the partition."""
        await self._hub.bridge.disarm(self._device.resource_id)

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        """Arm in Stay mode."""
        await self._hub.bridge.arm_stay(self._device.resource_id)

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        """Arm in Away mode."""
        await self._hub.bridge.arm_away(self._device.resource_id)

    async def async_alarm_arm_night(self, code: str | None = None) -> None:
        """Arm in Night mode."""
        await self._hub.bridge.arm_night(self._device.resource_id)
