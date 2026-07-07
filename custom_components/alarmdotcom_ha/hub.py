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
# Safety-net reconcile for the rare case of a WebSocket that stays *transport*-
# alive but silently stops delivering events. Real dead connections are already
# caught with zero polling by aiohttp's 60s heartbeat → reconnect → resync, so
# this backstop is deliberately infrequent and staleness-gated:
#   * the timer only fires every RECONCILE_INTERVAL, and
#   * it issues a REST refresh only when the socket has been silent for
#     STALE_AFTER (an active connection polls zero times).
# Net: healthy connections (which reconnect every few minutes) make no
# background REST calls at all; only a genuinely stuck socket — no frames and no
# reconnects for STALE_AFTER — is reconciled. The check itself is a free
# in-memory timestamp comparison, so it can run often without polling ADC.
# See GitHub issue #2 ("Connection to hub hangs").
RECONCILE_INTERVAL = timedelta(minutes=15)
STALE_AFTER = timedelta(minutes=30)


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
        self._unsub_reconcile = None
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
        self._unsub_reconcile = async_track_time_interval(
            self._hass,
            self._async_reconcile,
            RECONCILE_INTERVAL,
        )

    async def _async_reconcile(self, _now=None) -> None:
        """Re-fetch all device state from REST as a drift/stall safety net.

        Skips the REST call entirely unless the WebSocket looks genuinely stuck:
        connected but with no inbound frame — including reconnects — for
        ``STALE_AFTER``. Because a reconnect refreshes the "last message" time
        and the socket reconnects every few minutes, a healthy connection (even
        an idle, quiet one) never polls here. ``refresh_all()`` reconciles models
        in place and publishes updates only for devices whose state changed, so
        even when it does run there is no entity churn.
        """
        silent_for = self._bridge.websocket.seconds_since_last_message
        if (
            self._ws_connected
            and silent_for is not None
            and silent_for < STALE_AFTER.total_seconds()
        ):
            return  # healthy connection — no need to poll
        try:
            await self._bridge.refresh_all()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            log.debug("Reconcile failed (will retry next interval): %s", err)
        except Exception:
            log.exception("Unexpected error during reconcile")

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
            # NOTE: we deliberately do NOT refresh_all() on every reconnect. The
            # WebSocket cycles (fresh JWT) every ~5 minutes by design, so doing
            # so would mean a full REST poll every few minutes. A reconnect also
            # refreshes the WS "last message" timestamp, which keeps the
            # staleness-gated backstop (below) from ever firing on a healthy —
            # even if quiet — connection. Genuinely stuck sockets (no messages
            # AND no reconnects) still get caught by that backstop.
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
        if self._unsub_reconcile is not None:
            self._unsub_reconcile()
            self._unsub_reconcile = None
        if self._unsub_connection is not None:
            self._unsub_connection()
            self._unsub_connection = None
        await self._bridge.stop()
        await self._session.close()
