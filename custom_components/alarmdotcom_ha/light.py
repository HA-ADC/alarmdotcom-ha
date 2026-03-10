"""Light platform for alarmdotcom_ha."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from pyadc.const import LightState
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
    """Set up light entities."""
    hub: AlarmHub = hass.data[DOMAIN][entry.entry_id][DATA_BRIDGE]
    async_add_entities(AdcLight(hub, light) for light in hub.bridge.lights.devices)


class AdcLight(AdcEntity[Light], LightEntity):
    """Alarm.com light as a HA light entity."""

    def __init__(self, hub: AlarmHub, light: Light) -> None:
        super().__init__(hub, light)
        self._attr_supported_color_modes = self._build_color_modes()
        self._attr_color_mode = next(iter(self._attr_supported_color_modes))

    def _build_color_modes(self) -> set[ColorMode]:
        modes: set[ColorMode] = set()
        if self._device.supports_rgb:
            modes.add(ColorMode.RGB)
        if self._device.supports_white_color:
            modes.add(ColorMode.COLOR_TEMP)
        if self._device.supports_dimming:
            modes.add(ColorMode.BRIGHTNESS)
        if not modes:
            modes.add(ColorMode.ONOFF)
        return modes

    @property
    def is_on(self) -> bool:
        """Return True when the light is on."""
        return self._device.state in (LightState.ON, LightState.LEVEL_CHANGE)

    @property
    def brightness(self) -> int | None:
        """Return brightness on 0-255 HA scale."""
        return self._device.brightness_pct

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return RGB color tuple."""
        return self._device.rgb_color

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on, optionally with brightness / color."""
        brightness_255: int | None = kwargs.get(ATTR_BRIGHTNESS)
        rgb: tuple[int, int, int] | None = kwargs.get(ATTR_RGB_COLOR)

        # Convert HA 0-255 brightness to ADC 1-99
        adc_brightness: int | None = None
        if brightness_255 is not None:
            adc_brightness = max(1, min(99, round(brightness_255 / 255 * 99)))

        await self._hub.bridge.lights.turn_on(
            self._device.resource_id,
            brightness=adc_brightness,
            rgb=rgb,
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        await self._hub.bridge.lights.turn_off(self._device.resource_id)
