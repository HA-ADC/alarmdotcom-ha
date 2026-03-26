"""Camera platform for alarmdotcom_ha.

Each Alarm.com camera is exposed as a HA ``CameraEntity``.

**Still images** are served via ``video/snapshots/{id}`` — a signed HTTPS URL
returned by the ADC relay that contains a JPEG frame.

**Live streaming** uses WebRTC via the ADC Janus Gateway proxy:

1. HA frontend sends an SDP offer to ``async_handle_async_webrtc_offer``.
2. We fetch fresh Janus credentials (``janusGatewayUrl``, ``janusToken``,
   ``iceServers``) from ``video/videoSources/liveVideoSources/{id}``.
3. We relay the SDP offer to Janus (client-offer mode) and get an SDP answer.
4. The SDP answer is sent back to the HA frontend via ``send_message``.
5. Trickle ICE candidates are forwarded browser → Janus in real time.
6. The browser and Janus negotiate a direct P2P (or relayed) media path —
   no media passes through HA.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from homeassistant.components.camera import Camera as HaCamera, CameraEntityFeature
from homeassistant.components.camera.webrtc import (
    WebRTCAnswer,
    WebRTCClientConfiguration,
    WebRTCError,
    WebRTCSendMessage,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from webrtc_models import RTCConfiguration, RTCIceCandidateInit, RTCIceServer

from pyadc.janus import JanusError, JanusSession
from pyadc.models.camera import Camera

from .const import DATA_BRIDGE, DOMAIN
from .entity import AdcEntity
from .hub import AlarmHub

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Alarm.com camera entities."""
    hub: AlarmHub = hass.data[DOMAIN][entry.entry_id][DATA_BRIDGE]
    async_add_entities(
        AdcCamera(hub, camera) for camera in hub.bridge.cameras.devices
    )


class AdcCamera(AdcEntity[Camera], HaCamera):
    """An Alarm.com camera entity with WebRTC live streaming via Janus Gateway.

    Still images are served via the ADC relay snapshot endpoint.
    Live video uses HA's native WebRTC path — no RTSP passthrough required.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:cctv"

    def __init__(self, hub: AlarmHub, device: Camera) -> None:
        AdcEntity.__init__(self, hub, device)
        HaCamera.__init__(self)
        self._attr_unique_id = f"{device.resource_id}_camera"
        self._attr_name = None  # entity name = device name
        self._attr_supported_features = CameraEntityFeature.STREAM
        # Active Janus sessions keyed by HA WebRTC session_id
        self._janus_sessions: dict[str, JanusSession] = {}
        # Pre-queue for trickle candidates that arrive before JanusSession is ready.
        # Created at the start of async_handle_async_webrtc_offer so candidates
        # arriving during async gaps (e.g. source fetch) are not dropped.
        self._pending_candidates: dict[str, list[tuple]] = {}

    # ------------------------------------------------------------------ #
    # Still image
    # ------------------------------------------------------------------ #

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a JPEG snapshot fetched from the ADC relay."""
        url = await self._hub.bridge.cameras.get_snapshot_url(self._device)
        if not url:
            return None
        try:
            async with self._hub.bridge.client._session.get(
                url,
                headers=self._hub.bridge.client._build_headers(),
                timeout=15,
            ) as resp:
                if resp.status != 200:
                    log.debug(
                        "Camera %s snapshot HTTP %s",
                        self._device.resource_id,
                        resp.status,
                    )
                    return None
                return await resp.read()
        except Exception as exc:
            log.debug("Camera %s snapshot error: %s", self._device.resource_id, exc)
            return None

    async def stream_source(self) -> str | None:
        """Not used — live video is handled via WebRTC."""
        return None

    # ------------------------------------------------------------------ #
    # WebRTC live video
    # ------------------------------------------------------------------ #

    def _async_get_webrtc_client_configuration(self) -> WebRTCClientConfiguration:
        """Return ICE server config fetched from the ADC liveVideoSource.

        Called synchronously by HA before offering the WebRTC session to the
        frontend.  We use the cached ``live_video_source`` on the device model
        if available; the ICE servers are refreshed for each
        ``async_handle_async_webrtc_offer`` call anyway.
        """
        source = self._device.live_video_source
        if source and source.ice_servers:
            try:
                raw = json.loads(source.ice_servers)
                ice_servers = [
                    RTCIceServer(
                        urls=s.get("urls", []),
                        username=s.get("username"),
                        credential=s.get("credential"),
                    )
                    for s in raw
                ]
                return WebRTCClientConfiguration(
                    configuration=RTCConfiguration(ice_servers=ice_servers)
                )
            except Exception as exc:
                log.debug("Failed to parse ICE servers: %s", exc)
        return WebRTCClientConfiguration()

    async def async_handle_async_webrtc_offer(
        self,
        offer_sdp: str,
        session_id: str,
        send_message: WebRTCSendMessage,
    ) -> None:
        """Handle a WebRTC offer from the HA frontend.

        1. Fetch fresh Janus credentials from ADC.
        2. Connect to the Janus Gateway WebSocket.
        3. Send the browser's SDP offer to Janus (client-offer mode).
        4. Relay the SDP answer back to the browser via ``send_message``.

        The browser then connects directly to the Janus server for media;
        no video/audio data flows through HA.
        """
        log.debug(
            "Camera %s: handling WebRTC offer for session %s",
            self._device.resource_id,
            session_id,
        )

        # Initialize pre-queue IMMEDIATELY (before any await) so that trickle
        # candidates arriving during the async source-fetch gap are buffered.
        self._pending_candidates[session_id] = []

        # Fetch fresh liveVideoSource (Janus token expires after ~1 hour)
        source = await self._hub.bridge.cameras.get_live_video_source(self._device)
        if not source or not source.janus_gateway_url or not source.janus_token:
            log.warning(
                "Camera %s: no Janus credentials available", self._device.resource_id
            )
            send_message(
                WebRTCError("janus_unavailable", "Camera stream credentials unavailable")
            )
            return

        if not source.proxy_url:
            log.warning(
                "Camera %s: no proxy_url in liveVideoSource", self._device.resource_id
            )
            send_message(
                WebRTCError("janus_unavailable", "Camera stream proxy URL unavailable")
            )
            return

        # Parse ICE servers for JanusSession
        ice_servers: list[dict] = []
        if source.ice_servers:
            try:
                ice_servers = json.loads(source.ice_servers)
            except Exception:
                pass

        http_session = self._hub.bridge.client._session
        janus = JanusSession(
            source.janus_gateway_url,
            source.janus_token,
            source.proxy_url,
            ice_servers,
        )

        # Register a stopped callback so Janus can tear down the session
        # from our dict if the stream dies (RTSP timeout, camera offline, etc.).
        # This gives the browser a clean disconnect rather than a frozen frame.
        def _on_janus_stopped() -> None:
            log.debug(
                "Camera %s: Janus stream stopped — removing session %s",
                self._device.resource_id,
                session_id,
            )
            self._pending_candidates.pop(session_id, None)
            self._janus_sessions.pop(session_id, None)

        janus._on_stopped = _on_janus_stopped

        # Store the session BEFORE calling start() so that trickle ICE candidates
        # from the browser (which arrive almost immediately after we process the offer)
        # are queued in janus._browser_trickle_queue rather than dropped.
        self._janus_sessions[session_id] = janus

        # Drain any candidates that arrived before JanusSession was ready.
        for (cand, mid, idx) in self._pending_candidates.pop(session_id, []):
            log.debug(
                "Camera %s: draining pre-queued candidate for session %s: %s",
                self._device.resource_id, session_id, cand,
            )
            try:
                await janus.add_ice_candidate(cand, sdp_mid=mid, sdp_m_line_index=idx)
            except Exception as exc:
                log.debug("Camera %s: pre-queue drain ICE error: %s", self._device.resource_id, exc)

        try:
            answer_sdp = await janus.start(offer_sdp, http_session)
        except JanusError as exc:
            log.error(
                "Camera %s: Janus error: %s", self._device.resource_id, exc
            )
            self._janus_sessions.pop(session_id, None)
            self._pending_candidates.pop(session_id, None)
            await janus.close()
            send_message(WebRTCError("janus_error", str(exc)))
            return
        except Exception as exc:
            log.error(
                "Camera %s: unexpected WebRTC error: %s",
                self._device.resource_id,
                exc,
            )
            self._janus_sessions.pop(session_id, None)
            self._pending_candidates.pop(session_id, None)
            await janus.close()
            send_message(WebRTCError("webrtc_error", str(exc)))
            return

        send_message(WebRTCAnswer(answer=answer_sdp))
        log.debug(
            "Camera %s: WebRTC session %s established",
            self._device.resource_id,
            session_id,
        )

    async def async_on_webrtc_candidate(
        self, session_id: str, candidate: RTCIceCandidateInit
    ) -> None:
        """Forward a trickle ICE candidate from the browser to aiortc."""
        log.debug(
            "Camera %s: browser trickle candidate session=%s candidate=%s",
            self._device.resource_id, session_id, candidate,
        )
        janus = self._janus_sessions.get(session_id)
        if janus:
            try:
                await janus.add_ice_candidate(
                    candidate.candidate if candidate.candidate else None,
                    sdp_mid=candidate.sdp_mid,
                    sdp_m_line_index=candidate.sdp_m_line_index,
                )
            except Exception as exc:
                log.debug(
                    "Camera %s: trickle ICE error: %s",
                    self._device.resource_id,
                    exc,
                )
        elif session_id in self._pending_candidates:
            # Session is being set up — buffer until JanusSession is ready
            log.debug(
                "Camera %s: pre-queuing candidate for session %s (session not ready yet)",
                self._device.resource_id, session_id,
            )
            self._pending_candidates[session_id].append((
                candidate.candidate if candidate.candidate else None,
                candidate.sdp_mid,
                candidate.sdp_m_line_index,
            ))
        else:
            log.debug(
                "Camera %s: dropping candidate for unknown session %s",
                self._device.resource_id, session_id,
            )

    def close_webrtc_session(self, session_id: str) -> None:
        """Close the Janus WebSocket session when the browser disconnects."""
        self._pending_candidates.pop(session_id, None)
        janus = self._janus_sessions.pop(session_id, None)
        if janus:
            import asyncio
            asyncio.create_task(janus.close())

