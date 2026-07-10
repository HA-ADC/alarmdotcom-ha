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

import asyncio
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

from pyadc.janus import HAS_AIORTC, JanusError, JanusSession
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
        # HA's native WebRTC path needs the aiortc bridge (see janus.py).
        # Without it, don't advertise STREAM — the more-info dialog then shows
        # the snapshot instead of an always-failing stream attempt; live view is
        # available through the bundled adc-webrtc-card instead.
        self._attr_supported_features = (
            CameraEntityFeature.STREAM if HAS_AIORTC else CameraEntityFeature(0)
        )
        # Active Janus sessions keyed by HA WebRTC session_id
        self._janus_sessions: dict[str, JanusSession] = {}
        # Pre-queue for trickle candidates that arrive before JanusSession is ready.
        # Created at the start of async_handle_async_webrtc_offer so candidates
        # arriving during async gaps (e.g. source fetch) are not dropped.
        self._pending_candidates: dict[str, list[tuple]] = {}
        # Which relay endpoint actually delivers video for this camera, learned
        # empirically: some ADC cameras only stream via the HD relay, others
        # only via the SD relay.  None = not yet known (start with HD).
        self._pref_hd: bool | None = None
        # Keep strong refs to per-session verify/fallback tasks.
        self._verify_tasks: dict[str, asyncio.Task] = {}
        # Snapshot cache: dashboards poll camera_proxy every ~10 s, and every
        # uncached call makes the physical camera capture a fresh JPEG.  That
        # load is heavy enough to drop the camera's RTSP stream ("Device
        # Connection Dropped"), so snapshots are throttled here and never
        # fetched while a live WebRTC session is running.
        self._snapshot_cache: bytes | None = None
        self._snapshot_ts: float = 0.0

    # ------------------------------------------------------------------ #
    # Still image
    # ------------------------------------------------------------------ #

    _SNAPSHOT_CACHE_TTL_S = 45.0

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a JPEG snapshot fetched from the ADC relay.

        Snapshots are cached for ``_SNAPSHOT_CACHE_TTL_S`` and served stale
        while a live WebRTC session is active — each uncached call makes the
        camera capture a new JPEG, which can drop its live RTSP stream.
        """
        now = asyncio.get_running_loop().time()
        if self._snapshot_cache is not None and (
            self._janus_sessions or now - self._snapshot_ts < self._SNAPSHOT_CACHE_TTL_S
        ):
            return self._snapshot_cache

        url = await self._hub.bridge.cameras.get_snapshot_url(self._device)
        if not url:
            return self._snapshot_cache
        try:
            image = await self._hub.bridge.client.fetch_bytes(url)
        except Exception as exc:
            log.debug("Camera %s snapshot error: %s", self._device.resource_id, exc)
            return self._snapshot_cache
        if image:
            self._snapshot_cache = image
            self._snapshot_ts = now
        return image or self._snapshot_cache

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

        # Live WebRTC streaming needs the optional aiortc extra, which conflicts
        # with HA core's av>=17. When it's absent this is an expected degradation
        # (snapshots still work), not a fault — surface it to the frontend and
        # log at WARNING rather than letting a Janus attempt raise an ERROR on
        # every stream request.
        if not HAS_AIORTC:
            log.warning(
                "Camera %s: live WebRTC streaming unavailable (aiortc not "
                "installed); snapshots still work.",
                self._device.resource_id,
            )
            send_message(
                WebRTCError(
                    "streaming_unavailable",
                    "Live streaming is unavailable (aiortc not installed).",
                )
            )
            return

        # Initialize pre-queue IMMEDIATELY (before any await) so that trickle
        # candidates arriving during the async source-fetch gap are buffered.
        self._pending_candidates[session_id] = []

        # Fetch fresh liveVideoSource (Janus token expires after ~1 hour).
        # Endpoint choice (HD vs SD relay) is per-camera: start from the
        # learned preference, defaulting to HD.  The verify task below falls
        # back to the other endpoint if this one never delivers video.
        use_hd = self._pref_hd if self._pref_hd is not None else True
        source = await self._hub.bridge.cameras.get_live_video_source(
            self._device, hd=use_hd
        )
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

        http_session = self._hub.bridge.client.session
        janus = JanusSession(
            source.janus_gateway_url,
            source.janus_token,
            source.proxy_url,
            ice_servers,
            # HD relays are continuously-running streams a viewer joins
            # mid-GOP, so SPS/PPS injection is always needed; SD relays start
            # fresh per viewer, where the API's spsAndPpsRequired is accurate
            # (forcing injection on can stall the SD ingest entirely).
            add_sps_pps=True if use_hd else source.sps_and_pps_required,
            name=self._device.mac_address,
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

        # Watch for video actually arriving; switch relay endpoint if not.
        task = asyncio.create_task(
            self._verify_stream(session_id, janus, use_hd),
            name=f"adc-verify-stream-{session_id}",
        )
        self._verify_tasks[session_id] = task
        task.add_done_callback(lambda _: self._verify_tasks.pop(session_id, None))

    _FIRST_FRAME_TIMEOUT_S = 10.0

    async def _verify_stream(
        self, session_id: str, janus: JanusSession, used_hd: bool
    ) -> None:
        """Fall back to the other ADC relay endpoint if no video arrives.

        Some cameras only deliver decodable video on the HD relay, others only
        on the SD relay (see async_handle_async_webrtc_offer).  If the first
        endpoint produces no decoded frame within the timeout, swap the Janus
        side to the other endpoint — the browser connection stays up, so the
        viewer just sees the stream start a few seconds later.  Whichever
        endpoint works is remembered for future sessions of this camera.
        """
        if await janus.wait_first_frame(self._FIRST_FRAME_TIMEOUT_S):
            self._pref_hd = used_hd
            return
        if session_id not in self._janus_sessions:
            return  # session already torn down
        other_hd = not used_hd
        log.warning(
            "Camera %s: no video from %s relay within %.0fs — switching to %s",
            self._device.resource_id,
            "HD" if used_hd else "SD",
            self._FIRST_FRAME_TIMEOUT_S,
            "HD" if other_hd else "SD",
        )
        try:
            source = await self._hub.bridge.cameras.get_live_video_source(
                self._device, hd=other_hd
            )
            if not source or not source.proxy_url:
                log.warning(
                    "Camera %s: fallback source unavailable", self._device.resource_id
                )
                return
            await janus.switch_source(
                source.proxy_url,
                gateway_url=source.janus_gateway_url,
                token=source.janus_token,
                add_sps_pps=True if other_hd else source.sps_and_pps_required,
            )
        except Exception as exc:
            log.warning(
                "Camera %s: stream fallback failed: %s", self._device.resource_id, exc
            )
            return
        if await janus.wait_first_frame(self._FIRST_FRAME_TIMEOUT_S + 5):
            self._pref_hd = other_hd
            log.info(
                "Camera %s: video flowing via %s relay",
                self._device.resource_id,
                "HD" if other_hd else "SD",
            )
        else:
            log.warning(
                "Camera %s: no video from either relay endpoint",
                self._device.resource_id,
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
        task = self._verify_tasks.pop(session_id, None)
        if task:
            task.cancel()
        janus = self._janus_sessions.pop(session_id, None)
        if janus:
            asyncio.create_task(janus.close())

