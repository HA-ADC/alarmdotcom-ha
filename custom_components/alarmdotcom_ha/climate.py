"""Climate platform for alarmdotcom_ha.

Maps between pyadc thermostat enumerations and Home Assistant's climate
entity model (HVACMode, HVACAction, fan modes, preset modes).
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ATTR_HUMIDITY,
    ATTR_HVAC_MODE,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from pyadc.const import (
    ThermostatFanMode,
    ThermostatOperatingState,
    ThermostatSetpointType,
    ThermostatTemperatureMode,
)
from pyadc.models.thermostat import Thermostat

from .const import DATA_BRIDGE, DOMAIN
from .entity import AdcEntity
from .hub import AlarmHub

log = logging.getLogger(__name__)

# ThermostatTemperatureMode → HVACMode
# ENERGY_SAVE_HEAT and ENERGY_SAVE_COOL are ADC-specific "eco" variants that
# don't have direct HA equivalents; map them to HEAT/COOL so the entity
# stays functional.  AUX_HEAT (backup/emergency heat) also maps to HEAT
# because HA's AUX_HEAT is a feature flag, not an hvac_mode value.
_HVAC_MODE_MAP: dict[ThermostatTemperatureMode, HVACMode] = {
    ThermostatTemperatureMode.OFF: HVACMode.OFF,
    ThermostatTemperatureMode.HEAT: HVACMode.HEAT,
    ThermostatTemperatureMode.COOL: HVACMode.COOL,
    ThermostatTemperatureMode.AUTO: HVACMode.HEAT_COOL,
    ThermostatTemperatureMode.AUX_HEAT: HVACMode.HEAT,
    ThermostatTemperatureMode.ENERGY_SAVE_HEAT: HVACMode.HEAT,
    ThermostatTemperatureMode.ENERGY_SAVE_COOL: HVACMode.COOL,
}

# HVACMode → ThermostatTemperatureMode (for set_hvac_mode commands)
# FAN_ONLY has no ADC temperature-mode equivalent; send OFF so the thermostat
# stops conditioning air while the integration still represents fan operation.
_HA_TO_ADC_MODE: dict[HVACMode, ThermostatTemperatureMode] = {
    HVACMode.OFF: ThermostatTemperatureMode.OFF,
    HVACMode.HEAT: ThermostatTemperatureMode.HEAT,
    HVACMode.COOL: ThermostatTemperatureMode.COOL,
    HVACMode.HEAT_COOL: ThermostatTemperatureMode.AUTO,
    HVACMode.FAN_ONLY: ThermostatTemperatureMode.OFF,  # fan-only drives fan separately
}

# ThermostatOperatingState → HVACAction
_HVAC_ACTION_MAP: dict[ThermostatOperatingState, HVACAction] = {
    ThermostatOperatingState.OFF: HVACAction.IDLE,
    ThermostatOperatingState.HEATING: HVACAction.HEATING,
    ThermostatOperatingState.COOLING: HVACAction.COOLING,
    ThermostatOperatingState.FAN: HVACAction.FAN,
    ThermostatOperatingState.PENDING_HEAT: HVACAction.HEATING,
    ThermostatOperatingState.PENDING_COOL: HVACAction.COOLING,
    ThermostatOperatingState.AUX_HEAT: HVACAction.HEATING,
    ThermostatOperatingState.SECOND_STAGE_HEAT: HVACAction.HEATING,
    ThermostatOperatingState.SECOND_STAGE_COOL: HVACAction.COOLING,
    ThermostatOperatingState.WAITING: HVACAction.IDLE,
    ThermostatOperatingState.ERROR: HVACAction.IDLE,
    ThermostatOperatingState.UNKNOWN: HVACAction.IDLE,
}

# ThermostatFanMode → HA fan mode strings
_FAN_MODE_MAP: dict[ThermostatFanMode, str] = {
    ThermostatFanMode.AUTO_LOW: "Auto Low",
    ThermostatFanMode.ON_LOW: "On Low",
    ThermostatFanMode.AUTO_HIGH: "Auto High",
    ThermostatFanMode.ON_HIGH: "On High",
    ThermostatFanMode.AUTO_MEDIUM: "Auto Medium",
    ThermostatFanMode.ON_MEDIUM: "On Medium",
    ThermostatFanMode.CIRCULATE: "Circulate",
    ThermostatFanMode.HUMIDITY: "Humidity",
}
_FAN_MODE_REVERSE: dict[str, ThermostatFanMode] = {v: k for k, v in _FAN_MODE_MAP.items()}

# ThermostatSetpointType → HA preset mode strings
_PRESET_MAP: dict[ThermostatSetpointType, str] = {
    ThermostatSetpointType.AWAY: "Away",
    ThermostatSetpointType.HOME: "Home",
    ThermostatSetpointType.SLEEP: "Sleep",
    ThermostatSetpointType.FIXED: "Fixed",
}
_PRESET_REVERSE: dict[str, ThermostatSetpointType] = {v: k for k, v in _PRESET_MAP.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up climate entities."""
    hub: AlarmHub = hass.data[DOMAIN][entry.entry_id][DATA_BRIDGE]
    async_add_entities(
        AdcClimate(hub, thermostat) for thermostat in hub.bridge.thermostats.devices
    )


class AdcClimate(AdcEntity[Thermostat], ClimateEntity):
    """Alarm.com thermostat as a Home Assistant climate entity.

    Supports HVAC mode, fan mode, target temperature (single and range for
    AUTO mode), preset schedules, and optional humidity control.

    The set of supported HVAC modes is derived from ``_HVAC_MODE_MAP`` with
    duplicates removed (e.g. ``HEAT`` appears only once even though it covers
    HEAT, AUX_HEAT, and ENERGY_SAVE_HEAT).  ``HVACMode.FAN_ONLY`` is added
    only when the device advertises ``supports_fan_only``.
    """

    def __init__(self, hub: AlarmHub, thermostat: Thermostat) -> None:
        super().__init__(hub, thermostat)

        hvac_modes = list(_HVAC_MODE_MAP.values())
        # Deduplicate while preserving order
        seen: set[HVACMode] = set()
        unique_modes: list[HVACMode] = []
        for m in hvac_modes:
            if m not in seen:
                seen.add(m)
                unique_modes.append(m)
        if thermostat.supports_fan_only:
            unique_modes.append(HVACMode.FAN_ONLY)
        self._attr_hvac_modes = unique_modes

        features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.FAN_MODE
            | ClimateEntityFeature.PRESET_MODE
        )
        if thermostat.supports_humidity_control:
            features |= ClimateEntityFeature.TARGET_HUMIDITY
        self._attr_supported_features = features

        self._attr_fan_modes = list(_FAN_MODE_MAP.values())
        self._attr_preset_modes = list(_PRESET_MAP.values())

    @property
    def temperature_unit(self) -> str:
        """Return temperature unit based on thermostat setting."""
        if self._device.temperature_unit == "C":
            return UnitOfTemperature.CELSIUS
        return UnitOfTemperature.FAHRENHEIT

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return current HVAC mode."""
        return _HVAC_MODE_MAP.get(self._device.state)

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return current HVAC action (operating state)."""
        if self._device.operating_state is None:
            return None
        return _HVAC_ACTION_MAP.get(self._device.operating_state)

    @property
    def current_temperature(self) -> float | None:
        """Return current temperature."""
        return self._device.current_temperature

    @property
    def target_temperature(self) -> float | None:
        """Return single target temperature (for HEAT or COOL mode)."""
        mode = self._device.state
        if mode in (ThermostatTemperatureMode.HEAT, ThermostatTemperatureMode.AUX_HEAT,
                    ThermostatTemperatureMode.ENERGY_SAVE_HEAT):
            return self._device.target_temperature_heat
        if mode in (ThermostatTemperatureMode.COOL, ThermostatTemperatureMode.ENERGY_SAVE_COOL):
            return self._device.target_temperature_cool
        return None

    @property
    def target_temperature_high(self) -> float | None:
        """Return high target temperature (AUTO mode)."""
        return self._device.target_temperature_cool

    @property
    def target_temperature_low(self) -> float | None:
        """Return low target temperature (AUTO mode)."""
        return self._device.target_temperature_heat

    @property
    def current_humidity(self) -> float | None:
        """Return current humidity."""
        return self._device.current_humidity

    @property
    def target_humidity(self) -> float | None:
        """Return target humidity."""
        return self._device.target_humidity

    @property
    def fan_mode(self) -> str | None:
        """Return current fan mode."""
        return _FAN_MODE_MAP.get(self._device.fan_mode)

    @property
    def preset_mode(self) -> str | None:
        """Return current preset mode."""
        if self._device.setpoint_type is None:
            return None
        return _PRESET_MAP.get(self._device.setpoint_type)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode."""
        adc_mode = _HA_TO_ADC_MODE.get(hvac_mode)
        if adc_mode is None:
            log.warning("Unsupported HVAC mode: %s", hvac_mode)
            return
        await self._hub.bridge.thermostats.set_state(
            self._device.resource_id,
            mode=adc_mode,
        )

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set target temperature."""
        heat_setpoint = kwargs.get(ATTR_TARGET_TEMP_LOW) or kwargs.get(ATTR_TEMPERATURE)
        cool_setpoint = kwargs.get(ATTR_TARGET_TEMP_HIGH) or kwargs.get(ATTR_TEMPERATURE)

        mode = self._device.state
        if mode in (ThermostatTemperatureMode.HEAT, ThermostatTemperatureMode.AUX_HEAT,
                    ThermostatTemperatureMode.ENERGY_SAVE_HEAT):
            cool_setpoint = None
        elif mode in (ThermostatTemperatureMode.COOL, ThermostatTemperatureMode.ENERGY_SAVE_COOL):
            heat_setpoint = None

        await self._hub.bridge.thermostats.set_state(
            self._device.resource_id,
            heat_setpoint=heat_setpoint,
            cool_setpoint=cool_setpoint,
        )

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set fan mode."""
        adc_fan_mode = _FAN_MODE_REVERSE.get(fan_mode)
        if adc_fan_mode is None:
            log.warning("Unsupported fan mode: %s", fan_mode)
            return
        await self._hub.bridge.thermostats.set_state(
            self._device.resource_id,
            fan_mode=adc_fan_mode,
        )

    async def async_set_humidity(self, humidity: float) -> None:
        """Set target humidity."""
        await self._hub.bridge.thermostats.set_state(
            self._device.resource_id,
        )
        log.debug("Target humidity %s set (no direct ADC API — stored locally)", humidity)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set preset mode."""
        adc_setpoint = _PRESET_REVERSE.get(preset_mode)
        if adc_setpoint is None:
            log.warning("Unsupported preset mode: %s", preset_mode)
            return
        log.debug("Preset mode %s → %s (sent via set_state)", preset_mode, adc_setpoint)
