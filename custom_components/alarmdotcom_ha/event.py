"""Event platform for alarmdotcom_ha — Z-Wave Central Scene events on lights/switches.

Mirrors HA's Z-Wave JS integration: every Alarm.com light or switch gets one
event entity per scene, ``Scene 001`` (on/up paddle) and ``Scene 002``
(off/down paddle), whose event types are the Z-Wave key attribute names
(``KeyPressed``, ``KeyReleased``, ``KeyHeldDown``, ``KeyPressed2x`` …
``KeyPressed5x``).

ADC reports these as ``ButtonPressed`` (EventTypeEnum 163) MonitorEvents with
``EventValue = 10000 * keyAttribute + sceneNumber``; pyadc decodes them and
publishes on ``EventBrokerTopic.DEVICE_EVENT``.

Automations can trigger on them in three equivalent ways:

* Device trigger: "Scene action on Scene 002" (optionally with a Value).
* Entity state trigger on ``event.<light>_scene_002`` with
  ``attribute: event_type``.
* Raw bus event ``alarmdotcom_ha_event`` (fired for *every* non-state event,
  including scenes/key attributes not modelled here).
"""

from __future__ import annotations

import logging

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from pyadc.events import DeviceEventMessage, EventBrokerTopic
from pyadc.models.light import Light

from .const import DATA_BRIDGE, DOMAIN, SCENE_EVENT_TYPES, SCENES, scene_id
from .entity import AdcEntity
from .hub import AlarmHub

log = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Scene 001/002 event entities for every ADC light and switch."""
    hub: AlarmHub = hass.data[DOMAIN][entry.entry_id][DATA_BRIDGE]
    async_add_entities(
        AdcSceneEvent(hub, light, scene)
        for light in hub.bridge.lights.devices
        for scene in SCENES
    )


class AdcSceneEvent(AdcEntity[Light], EventEntity):
    """One Central Scene (paddle) of an Alarm.com Z-Wave light/switch."""

    _attr_event_types = list(SCENE_EVENT_TYPES)
    _attr_translation_key = "scene"

    def __init__(self, hub: AlarmHub, device: Light, scene: int) -> None:
        super().__init__(hub, device)
        self._scene = scene
        self._attr_unique_id = f"{device.resource_id}_scene_{scene_id(scene)}"
        self._attr_translation_placeholders = {"scene": scene_id(scene)}
        # Let the translation key supply the name ("Scene 001"); the base class
        # sets name=None, which would make this the device's main entity.
        del self._attr_name

    async def async_added_to_hass(self) -> None:
        """Subscribe to momentary device events in addition to state updates."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._hub.bridge.event_broker.subscribe(
                [EventBrokerTopic.DEVICE_EVENT],
                self._handle_device_event,
                device_id=self._device.resource_id,
            )
        )

    def _handle_device_event(self, message: object) -> None:
        """Fire the HA event when this scene reports a known key attribute."""
        if not isinstance(message, DeviceEventMessage):
            return
        if message.button != self._scene or message.action not in self._attr_event_types:
            return
        self._trigger_event(message.action, {"key_attribute": message.key_attribute})
        self.async_write_ha_state()
