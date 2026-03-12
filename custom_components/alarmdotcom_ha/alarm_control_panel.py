"""Alarm control panel platform for alarmdotcom_ha."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_platform
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

# Schema shared by all extended-arming services
_ARM_OPTIONS_SCHEMA = {
    vol.Optional("silent_arming", default=False): bool,
    vol.Optional("force_bypass", default=False): bool,
    vol.Optional("no_entry_delay", default=False): bool,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up alarm control panel entities and register extended arming services."""
    hub: AlarmHub = hass.data[DOMAIN][entry.entry_id][DATA_BRIDGE]
    async_add_entities(
        AdcAlarmControlPanel(hub, partition)
        for partition in hub.bridge.partitions.devices
    )

    # Register custom entity services for extended arming options
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        "arm_away_options",
        _ARM_OPTIONS_SCHEMA,
        "async_alarm_arm_away_options",
    )
    platform.async_register_entity_service(
        "arm_stay_options",
        _ARM_OPTIONS_SCHEMA,
        "async_alarm_arm_stay_options",
    )
    platform.async_register_entity_service(
        "arm_night_options",
        _ARM_OPTIONS_SCHEMA,
        "async_alarm_arm_night_options",
    )


class AdcAlarmControlPanel(AdcEntity[Partition], AlarmControlPanelEntity):
    """Alarm.com partition as a HA alarm control panel.

    Optimistic transitional states: arming/disarming commands immediately
    show ARMING or DISARMING in the UI. The WS EventWSMessage confirmation
    triggers _handle_update which clears the transition and shows final state.
    """

    _attr_code_arm_required = False

    def __init__(self, hub: AlarmHub, partition: Partition) -> None:
        super().__init__(hub, partition)
        self._transitional_state: AlarmControlPanelState | None = None
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
        """Return transitional state while arming/disarming, otherwise actual state."""
        if self._transitional_state is not None:
            return self._transitional_state
        return _ARMING_STATE_MAP.get(self._device.state)

    def _handle_update(self, message: object) -> None:
        """WS confirmation arrived — clear transition then refresh."""
        self._transitional_state = None
        super()._handle_update(message)

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        """Disarm the partition."""
        self._transitional_state = AlarmControlPanelState.DISARMING
        self.async_write_ha_state()
        await self._hub.bridge.disarm(self._device.resource_id)

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        """Arm in Stay mode."""
        self._transitional_state = AlarmControlPanelState.ARMING
        self.async_write_ha_state()
        await self._hub.bridge.arm_stay(self._device.resource_id)

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        """Arm in Away mode."""
        self._transitional_state = AlarmControlPanelState.ARMING
        self.async_write_ha_state()
        await self._hub.bridge.arm_away(self._device.resource_id)

    async def async_alarm_arm_night(self, code: str | None = None) -> None:
        """Arm in Night mode."""
        self._transitional_state = AlarmControlPanelState.ARMING
        self.async_write_ha_state()
        await self._hub.bridge.arm_night(self._device.resource_id)

    # --- Extended arming services (registered in async_setup_entry) ---

    async def async_alarm_arm_away_options(
        self,
        silent_arming: bool = False,
        force_bypass: bool = False,
        no_entry_delay: bool = False,
    ) -> None:
        """Arm Away with optional silent/force-bypass/no-entry-delay flags."""
        self._transitional_state = AlarmControlPanelState.ARMING
        self.async_write_ha_state()
        await self._hub.bridge.partitions.arm_away(
            self._device.resource_id,
            silent=silent_arming,
            force_bypass=force_bypass,
            no_entry_delay=no_entry_delay,
        )

    async def async_alarm_arm_stay_options(
        self,
        silent_arming: bool = False,
        force_bypass: bool = False,
        no_entry_delay: bool = False,
    ) -> None:
        """Arm Stay with optional silent/force-bypass/no-entry-delay flags."""
        self._transitional_state = AlarmControlPanelState.ARMING
        self.async_write_ha_state()
        await self._hub.bridge.partitions.arm_stay(
            self._device.resource_id,
            silent=silent_arming,
            force_bypass=force_bypass,
            no_entry_delay=no_entry_delay,
        )

    async def async_alarm_arm_night_options(
        self,
        silent_arming: bool = False,
        force_bypass: bool = False,
        no_entry_delay: bool = False,
    ) -> None:
        """Arm Night with optional silent/force-bypass/no-entry-delay flags."""
        self._transitional_state = AlarmControlPanelState.ARMING
        self.async_write_ha_state()
        await self._hub.bridge.partitions.arm_night(
            self._device.resource_id,
            silent=silent_arming,
            force_bypass=force_bypass,
            no_entry_delay=no_entry_delay,
        )
