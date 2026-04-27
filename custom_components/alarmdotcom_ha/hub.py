"""Hub for the alarmdotcom_ha integration — wraps pyadc AlarmBridge.

``AlarmHub`` is a thin lifecycle adapter between the Home Assistant config
entry and the pyadc library.  It:

* Creates a dedicated :class:`aiohttp.ClientSession` for all ADC traffic.
* Instantiates :class:`~pyadc.AlarmBridge` and calls ``initialize()`` /
  ``start_websocket()`` in :meth:`initialize`.
* Subscribes to ``CONNECTION_EVENT`` to detect when the WebSocket enters the
  DEAD state and schedules a config-entry reload to re-authenticate.  Reloads
  are rate-limited to at most one every ``DEAD_RELOAD_COOLDOWN_S`` seconds to
  prevent cascading re-auth storms during backend outages.
* Polls the Water Dragon (water meter) every hour since it does not receive
  real-time WebSocket events.
* Tears everything down cleanly in :meth:`shutdown`.

``connected`` property reflects whether the WebSocket is currently in
``CONNECTED`` state and can be used in diagnostics or sensor availability.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import TYPE_CHECKING

import asyncio
import aiohttp

from pyadc import AlarmBridge
from pyadc.events import EventBrokerTopic, ResourceEventMessage
from pyadc.websocket.client import ConnectionEvent, WebSocketState

from homeassistant.helpers.event import async_track_time_interval

from .const import CONF_SEAMLESS_TOKEN, WATER_METER_DEVICE_TYPE

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.config_entries import ConfigEntry

log = logging.getLogger(__name__)

WATER_METER_POLL_INTERVAL = timedelta(hours=1)
# Minimum seconds between config-entry reloads triggered by the DEAD state.
# Prevents a cascade of full re-auth attempts when the backend is degraded.
DEAD_RELOAD_COOLDOWN_S: float = 300.0


class AlarmHub:
    """Wraps AlarmBridge and integrates it with the Home Assistant lifecycle."""

    def __init__(
        self,
        hass: "HomeAssistant",
        entry: "ConfigEntry",
        username: str,
        password: str,
        mfa_cookie: str = "",
        seamless_token: str = "",
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
            seamless_token=seamless_token,
            base_url=base_url,
        )
        self._unsub_connection = None
        self._unsub_water_poll = None
        self._ws_connected: bool = False
        self._last_dead_reload_time: float = 0.0

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
        self._unsub_water_poll = async_track_time_interval(
            self._hass,
            self._async_poll_water_meters,
            WATER_METER_POLL_INTERVAL,
        )

    async def _async_poll_water_meters(self, _now=None) -> None:
        """Refresh water meter data and notify HA entities."""
        try:
            meters = await self._bridge.water_meters.fetch_all()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            log.warning("Water meter poll failed: %s", err)
            return
        except Exception:
            log.exception("Unexpected error during water meter poll")
            return

        for meter in meters:
            self._bridge.event_broker.publish(
                ResourceEventMessage(
                    device_id=meter.resource_id,
                    device_type=WATER_METER_DEVICE_TYPE,
                )
            )

    def _handle_connection_event(self, message: ConnectionEvent) -> None:
        if message.current_state is WebSocketState.CONNECTED:
            self._ws_connected = True
            # Token may have rotated during a mid-session re-auth — persist it
            # so the next restart can use the seamless path instead of full login.
            self._hass.async_create_task(self._async_persist_seamless_token())
        elif message.current_state in (
            WebSocketState.DEAD,
            WebSocketState.DISCONNECTED,
        ):
            self._ws_connected = False

        if message.current_state is WebSocketState.DEAD:
            now = time.monotonic()
            elapsed = now - self._last_dead_reload_time
            if elapsed >= DEAD_RELOAD_COOLDOWN_S:
                self._last_dead_reload_time = now
                log.warning(
                    "WebSocket entered DEAD state — "
                    "scheduling config entry reload to re-authenticate."
                )
                self._hass.async_create_task(
                    self._reload_after_shutdown()
                )
            else:
                remaining = int(DEAD_RELOAD_COOLDOWN_S - elapsed)
                log.warning(
                    "WebSocket DEAD again but reload cooldown active "
                    "(%ds remaining) — skipping reload, pyadc will keep retrying.",
                    remaining,
                )

    async def _async_persist_seamless_token(self) -> None:
        """Persist the seamless login token to the config entry if it has changed.

        Called after every CONNECTED event so a token rotated during a
        mid-session re-auth is saved before the next HA restart.
        """
        token = self._bridge.auth.seamless_token
        if token and token != self._entry.data.get(CONF_SEAMLESS_TOKEN, ""):
            updated = {**self._entry.data, CONF_SEAMLESS_TOKEN: token}
            self._hass.config_entries.async_update_entry(self._entry, data=updated)
            log.debug("Seamless login token persisted (rotated)")

    async def _reload_after_shutdown(self) -> None:
        """Tear down the current session, then trigger a config-entry reload.

        Called when the WebSocket enters the DEAD state.  Shutting down first
        ensures stale connections and zombie tasks are cleaned up before HA
        re-creates the config entry.
        """
        await self.shutdown()
        await self._hass.config_entries.async_reload(self._entry.entry_id)

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
