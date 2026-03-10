"""Base entity for the alarmdotcom_ha Home Assistant integration.

:class:`AdcEntity` subscribes to the pyadc :class:`~pyadc.events.EventBroker`
for per-device ``RESOURCE_UPDATED`` events and calls
:meth:`~homeassistant.helpers.entity.Entity.async_write_ha_state` to push
state changes into HA.

**Event loop threading note:**  The EventBroker dispatches callbacks
synchronously from the WebSocket processor task, which runs on the same
asyncio event loop as Home Assistant.  Therefore ``_handle_update`` is always
called on the correct loop and can safely call ``async_write_ha_state``
without any thread-safety concerns.
"""

from __future__ import annotations

import logging
from typing import Generic, TypeVar

from homeassistant.helpers.entity import DeviceInfo, Entity

from pyadc.events import EventBrokerTopic
from pyadc.models.base import AdcDeviceResource

from .const import DOMAIN
from .hub import AlarmHub

log = logging.getLogger(__name__)

AdcDeviceT = TypeVar("AdcDeviceT", bound=AdcDeviceResource)


class AdcEntity(Entity, Generic[AdcDeviceT]):
    """Base class for all alarmdotcom_ha HA entities.

    Handles EventBroker subscription lifecycle (subscribe on add, unsubscribe
    on remove) and state-push via ``async_write_ha_state``.

    ``should_poll = False`` — all state updates are event-driven.

    Subclasses access the underlying pyadc model via :attr:`device`.

    Attributes:
        _hub: The :class:`~.hub.AlarmHub` instance.
        _device: The typed pyadc device model (e.g.
            :class:`~pyadc.models.partition.Partition`).
    """

    should_poll = False
    _attr_has_entity_name = True

    def __init__(self, hub: AlarmHub, device: AdcDeviceT) -> None:
        self._hub = hub
        self._device = device
        self._attr_unique_id = device.resource_id
        self._attr_name = device.name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.resource_id)},
            name=device.name,
            manufacturer="Alarm.com",
        )

    @property
    def device(self) -> AdcDeviceT:
        """Return the underlying pyadc device model."""
        return self._device

    @property
    def available(self) -> bool:
        """Return ``False`` when the WebSocket is disconnected or the device is disabled.

        An entity is considered unavailable in two cases:
        - The WebSocket connection is not in CONNECTED state (``hub.connected``
          returns ``False``), meaning state information may be stale.
        - The device has been administratively disabled in the ADC portal
          (``device.is_disabled``).
        """
        return self._hub.connected and not self._device.is_disabled

    async def async_added_to_hass(self) -> None:
        """Subscribe to resource update events for this device."""
        self._unsubscribe = self._hub.bridge.event_broker.subscribe(
            [EventBrokerTopic.RESOURCE_UPDATED],
            self._handle_update,
            device_id=self._device.resource_id,
        )

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from events when entity is removed."""
        if hasattr(self, "_unsubscribe") and self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    def _handle_update(self, message: object) -> None:
        """Push updated state to Home Assistant.

        Called synchronously by the EventBroker whenever this device's
        ``RESOURCE_UPDATED`` event fires.  The EventBroker dispatches from
        the WebSocket processor task, which runs on the same asyncio event
        loop as Home Assistant, so ``async_write_ha_state()`` is safe to
        call directly here without any scheduling.
        """
        self.async_write_ha_state()
