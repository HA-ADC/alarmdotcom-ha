"""Hub for the alarmdotcom_ha integration — wraps pyadc AlarmBridge.

``AlarmHub`` is a thin lifecycle adapter between the Home Assistant config
entry and the pyadc library.  It:

* Creates a dedicated :class:`aiohttp.ClientSession` for all ADC traffic.
* Instantiates :class:`~pyadc.AlarmBridge` and calls ``initialize()`` /
  ``start_websocket()`` in :meth:`initialize`.
* Subscribes to ``CONNECTION_EVENT`` to detect when the WebSocket enters the
  DEAD state (close code 1008 / JWT expiry) and schedules a config-entry
  reload so the integration re-authenticates from scratch.
* Polls the Water Dragon (water meter) every hour since it does not receive
  real-time WebSocket events.
* Tears everything down cleanly in :meth:`shutdown`.

``connected`` property reflects whether the WebSocket is currently in
``CONNECTED`` state and can be used in diagnostics or sensor availability.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

import aiohttp

from pyadc import AlarmBridge
from pyadc.events import EventBrokerTopic, ResourceEventMessage
from pyadc.websocket.client import ConnectionEvent, WebSocketState

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.config_entries import ConfigEntry

log = logging.getLogger(__name__)

WATER_METER_POLL_INTERVAL = timedelta(hours=1)


class AlarmHub:
    """Wraps AlarmBridge and integrates it with the Home Assistant lifecycle."""

    def __init__(
        self,
        hass,
        entry,
        username: str,
        password: str,
        mfa_cookie: str = "",
        base_url: str = "https://www.alarm.com",
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._session = aiohttp.ClientSession()
        self._bridge = AlarmBridge(
            self._session,
            username,
            password,
            mfa_cookie=mfa_cookie,
            base_url=base_url,
        )
        self._unsub_connection = None
        self._unsub_water_poll = None
        self._ws_connected: bool = False

    @property
    def bridge(self) -> AlarmBridge:
        return self._bridge

    @property
    def connected(self) -> bool:
        return self._ws_connected

    async def initialize(self) -> None:
        """Authenticate, load all device state, then start the WebSocket."""
        await self._bridge.initialize()
        self._unsub_connection = self._bridge.event_broker.subscribe(
            [EventBrokerTopic.CONNECTION_EVENT],
            self._handle_connection_event,
        )
        await self._bridge.start_websocket()

        await self._async_poll_water_meters()
        from homeassistant.helpers.event import async_track_time_interval
        self._unsub_water_poll = async_track_time_interval(
            self._hass,
            self._async_poll_water_meters,
            WATER_METER_POLL_INTERVAL,
        )

    async def _async_poll_water_meters(self, _now=None) -> None:
        """Refresh water meter data and notify HA entities."""
        try:
            meters = await self._bridge.water_meters.fetch_all()
        except Exception:
            log.exception("alarmdotcom_ha: water meter poll failed")
            return

        for meter in meters:
            self._bridge.event_broker.publish(
                ResourceEventMessage(
                    device_id=meter.resource_id,
                    device_type="water-meter",
                )
            )

    def _handle_connection_event(self, message: ConnectionEvent) -> None:
        if message.current_state is WebSocketState.CONNECTED:
            self._ws_connected = True
        elif message.current_state in (
            WebSocketState.DEAD,
            WebSocketState.DISCONNECTED,
        ):
            self._ws_connected = False

        if message.current_state is WebSocketState.DEAD:
            log.warning(
                "alarmdotcom_ha: WebSocket entered DEAD state (likely 1008 JWT expiry). "
                "Scheduling config entry reload to re-authenticate."
            )
            self._hass.async_create_task(
                self._hass.config_entries.async_reload(self._entry.entry_id)
            )

    async def shutdown(self) -> None:
        """Stop WebSocket, water poll, and close the HTTP session."""
        if self._unsub_water_poll is not None:
            self._unsub_water_poll()
            self._unsub_water_poll = None
        if self._unsub_connection is not None:
            self._unsub_connection()
            self._unsub_connection = None
        await self._bridge.stop()
        await self._session.close()
