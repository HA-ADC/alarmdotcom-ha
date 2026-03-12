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
    """Set up light entities (excludes plain on/off switches — those go to switch platform)."""
    hub: AlarmHub = hass.data[DOMAIN][entry.entry_id][DATA_BRIDGE]
    async_add_entities(
        AdcLight(hub, light)
        for light in hub.bridge.lights.devices
        if not light.is_switch
    )


class AdcLight(AdcEntity[Light], LightEntity):
    """Alarm.com light as a HA light entity."""

    def __init__(self, hub: AlarmHub, light: Light) -> None:
        super().__init__(hub, light)
        self._attr_supported_color_modes = self._build_color_modes()

    def _build_color_modes(self) -> set[ColorMode]:
        modes: set[ColorMode] = set()
        if self._device.supports_rgb:
            modes.add(ColorMode.RGB)
        if self._device.supports_white_color:
            modes.add(ColorMode.COLOR_TEMP)
        # BRIGHTNESS is only valid as a standalone mode — omit when any color
        # mode is already present (RGB and COLOR_TEMP already imply brightness).
        if self._device.supports_dimming and not modes:
            modes.add(ColorMode.BRIGHTNESS)
        if not modes:
            modes.add(ColorMode.ONOFF)
        return modes

    @property
    def color_mode(self) -> ColorMode:
        """Return the currently active color mode."""
        supported = self._attr_supported_color_modes
        if ColorMode.RGB in supported and self._device.rgb_color is not None:
            return ColorMode.RGB
        if ColorMode.COLOR_TEMP in supported and self._device.color_temp is not None:
            return ColorMode.COLOR_TEMP
        return next(iter(supported))

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

        # Optimistic update — reflect the change immediately in the UI.
        # The WebSocket confirmation will arrive shortly and override this.
        self._device.state = LightState.ON
        if adc_brightness is not None:
            self._device.brightness = adc_brightness
        if rgb is not None:
            self._device.rgb_color = rgb
        self.async_write_ha_state()

        await self._hub.bridge.lights.turn_on(
            self._device.resource_id,
            brightness=adc_brightness,
        )

        if rgb is not None:
            r, g, b = rgb
            hex_color = f"#{r:02X}{g:02X}{b:02X}"
            _format_code = {"RGBW": 1, "RGB": 2, "WARM_TO_COOL": 3, "HSV": 4}
            color_format = _format_code.get(self._device.light_color_format or "", 1)
            await self._hub.bridge.lights.set_color(
                self._device.resource_id,
                hex_color,
                color_format,
            )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        # Optimistic update — reflect the change immediately in the UI.
        self._device.state = LightState.OFF
        self.async_write_ha_state()

        await self._hub.bridge.lights.turn_off(self._device.resource_id)
