"""Hub for the alarmdotcom_ha integration — wraps pyadc AlarmBridge.

``AlarmHub`` is a thin lifecycle adapter between the Home Assistant config
entry and the pyadc library.  It:

* Creates a dedicated :class:`aiohttp.ClientSession` for all ADC traffic.
* Instantiates :class:`~pyadc.AlarmBridge` and calls ``initialize()`` /
  ``start_websocket()`` in :meth:`initialize`.
* Subscribes to ``CONNECTION_EVENT`` to detect when the WebSocket enters the
  DEAD state (close code 1008 / JWT expiry) and schedules a config-entry
  reload so the integration re-authenticates from scratch.
* Tears everything down cleanly in :meth:`shutdown`.

``connected`` property reflects whether the WebSocket is currently in
``CONNECTED`` state and can be used in diagnostics or sensor availability.
"""

from __future__ import annotations

import logging

import aiohttp

from pyadc import AlarmBridge
from pyadc.events import EventBrokerTopic
from pyadc.websocket.client import ConnectionEvent, WebSocketState

log = logging.getLogger(__name__)


class AlarmHub:
    """Wraps AlarmBridge and integrates it with the Home Assistant lifecycle.

    Attributes:
        bridge: The underlying :class:`~pyadc.AlarmBridge` instance.  Use
            this to access device controllers (``bridge.partitions``, etc.)
            or to subscribe to events via ``bridge.event_broker``.
    """

    def __init__(
        self,
        hass,
        entry,
        username: str,
        password: str,
        mfa_cookie: str = "",
    ) -> None:
        """Create an AlarmHub.

        Args:
            hass: Home Assistant instance.
            entry: Config entry associated with this hub.
            username: Alarm.com account e-mail.
            password: Alarm.com account password.
            mfa_cookie: Pre-stored two-factor auth cookie (skips OTP
                challenge when valid).
        """
        self._hass = hass
        self._entry = entry
        self._session = aiohttp.ClientSession()
        self._bridge = AlarmBridge(
            self._session,
            username,
            password,
            mfa_cookie=mfa_cookie,
        )
        self._unsub_connection: callable | None = None
        self._ws_connected: bool = False

    @property
    def bridge(self) -> AlarmBridge:
        """Return the underlying AlarmBridge instance."""
        return self._bridge

    @property
    def connected(self) -> bool:
        """Return True when the WebSocket is in CONNECTED state."""
        return self._ws_connected

    async def initialize(self) -> None:
        """Authenticate, load all device state, then start the WebSocket.

        Called once during config-entry setup.  On success the WebSocket
        is running and device entities can be registered.
        """
        await self._bridge.initialize()
        self._unsub_connection = self._bridge.event_broker.subscribe(
            [EventBrokerTopic.CONNECTION_EVENT],
            self._handle_connection_event,
        )
        await self._bridge.start_websocket()

    def _handle_connection_event(self, message: ConnectionEvent) -> None:
        """Handle WebSocket state changes from the EventBroker.

        Tracks ``_ws_connected`` for the :attr:`connected` property.  When
        the WebSocket transitions to DEAD (typically after receiving close
        code 1008 indicating JWT expiry or repeated connection failures),
        schedules a config-entry reload which tears down and restarts the
        integration — effectively re-authenticating the session.
        """
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
        """Stop WebSocket, keep-alive, and close the HTTP session."""
        if self._unsub_connection is not None:
            self._unsub_connection()
            self._unsub_connection = None
        await self._bridge.stop()
        await self._session.close()
