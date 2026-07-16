/**
 * adc-webrtc-card — live WebRTC viewer for alarmdotcom_ha cameras.
 *
 * Speaks the Janus streaming-plugin protocol directly from the browser to
 * ADC's Janus gateway (the same flow as ADC's own web player), so live view
 * needs no server-side WebRTC stack. Home Assistant only supplies short-lived
 * stream credentials via the `alarmdotcom_ha/camera_stream_info` WS command;
 * signaling and media go browser <-> ADC directly.
 *
 * In the streaming plugin Janus is the SDP *offerer*: the card creates a
 * dynamic mountpoint from the camera's proxy URL, `watch`es it, receives
 * Janus's offer, and answers with a receive-only RTCPeerConnection.
 *
 * Usage (manual card):
 *   type: custom:adc-webrtc-card
 *   entity: camera.front_doorbell
 *   # optional:
 *   # autoplay: true   (connect on load; reconnect forever on drops — kiosk mode)
 *
 * Dropped streams (Janus hangup, ICE failure, hourly token expiry) reconnect
 * automatically with exponential backoff. Without autoplay the card gives up
 * after a few attempts and shows tap-to-retry; with autoplay it never stops
 * trying (capped at one attempt per 30 s).
 */

const KEEPALIVE_INTERVAL_MS = 25000; // Janus sessions expire after 60s idle
const TX_TIMEOUT_MS = 20000;
const FIRST_FRAME_TIMEOUT_MS = 12000; // per relay; then fall back HD<->SD

// Auto-reconnect after a mid-stream drop (Janus hangup, ICE failure, token
// expiry, gateway WS close). Exponential backoff; each attempt fetches fresh
// credentials. Cards with `autoplay: true` (kiosk dashboards) never give up —
// they keep retrying at the capped interval; others stop after MAX_ATTEMPTS
// and show tap-to-retry.
const RECONNECT_BASE_MS = 2000;
const RECONNECT_MAX_DELAY_MS = 30000;
const RECONNECT_MAX_ATTEMPTS = 5;

class JanusStream {
  /**
   * @param {object} info  result of alarmdotcom_ha/camera_stream_info
   * @param {(stream: MediaStream) => void} onTrack
   * @param {(reason: string) => void} onStopped
   */
  constructor(info, onTrack, onStopped) {
    this._info = info;
    this._onTrack = onTrack;
    this._onStopped = onStopped;
    this._ws = null;
    this._pc = null;
    this._sessionId = null;
    this._handleId = null;
    this._streamId = null;
    this._pending = new Map(); // transaction -> {resolve, reject, wantJsep, timer}
    this._keepalive = null;
    this._closed = false;
    // Janus may trickle candidates right after its offer, before the
    // RTCPeerConnection exists — buffer and drain once remoteDescription is set.
    this._earlyCandidates = [];
  }

  async connect() {
    await this._openWebSocket();

    const created = await this._tx({ janus: "create" });
    this._sessionId = created.data.id;

    const attached = await this._tx({
      janus: "attach",
      session_id: this._sessionId,
      plugin: "janus.plugin.streaming",
    });
    this._handleId = attached.data.id;

    this._keepalive = setInterval(() => {
      this._fire({ janus: "keepalive", session_id: this._sessionId });
    }, KEEPALIVE_INTERVAL_MS);

    // Create a dynamic mountpoint from the camera's proxy URL. Field-for-field
    // the body ADC's own player sends (mirrors pyadc's JanusSession).
    const body = {
      request: "create",
      is_private: true,
      type: "rtp",
      media_uri: this._info.media_uri,
      add_sps_pps: !!this._info.add_sps_pps,
      is_virtual: false,
      video: true,
      videoport: 0,
      videopt: 126,
      videortpmap: "H264/90000",
      videofmtp: "profile-level-id=42e01f;packetization-mode=1",
    };
    if (this._info.mountpoint_name) body.name = this._info.mountpoint_name;

    const createResp = await this._tx({
      janus: "message",
      session_id: this._sessionId,
      handle_id: this._handleId,
      body,
    });
    const pluginData = createResp.plugindata?.data ?? {};
    if (pluginData.error) throw new Error(`Janus create error: ${pluginData.error}`);
    this._streamId = pluginData.stream?.id;
    if (!this._streamId) throw new Error("Janus create: no stream id in response");

    // watch (no JSEP) -> Janus responds with its SDP offer.
    const watchResp = await this._tx(
      {
        janus: "message",
        session_id: this._sessionId,
        handle_id: this._handleId,
        body: { request: "watch", id: this._streamId },
      },
      { wantJsep: true }
    );
    const offer = watchResp.jsep;
    if (!offer || offer.type !== "offer") {
      throw new Error("Expected Janus SDP offer");
    }

    this._pc = new RTCPeerConnection({
      iceServers: this._info.ice_servers ?? [],
    });
    this._pc.ontrack = (ev) => {
      const stream = ev.streams?.[0] ?? new MediaStream([ev.track]);
      this._onTrack(stream);
    };
    this._pc.onicecandidate = (ev) => {
      if (this._closed) return;
      if (ev.candidate) {
        this._fire({
          janus: "trickle",
          session_id: this._sessionId,
          handle_id: this._handleId,
          candidate: {
            candidate: ev.candidate.candidate,
            sdpMid: ev.candidate.sdpMid ?? "0",
            sdpMLineIndex: ev.candidate.sdpMLineIndex ?? 0,
          },
        });
      } else {
        this._fire({
          janus: "trickle",
          session_id: this._sessionId,
          handle_id: this._handleId,
          candidate: { completed: true },
        });
      }
    };
    this._pc.onconnectionstatechange = () => {
      const st = this._pc?.connectionState;
      if (this._closed) return;
      if (st === "failed") {
        this._onStopped("peer connection failed");
      } else if (st === "disconnected") {
        // Often transient (a lost packet burst) — give ICE a few seconds to
        // recover on its own before tearing down and reconnecting.
        clearTimeout(this._disconnectTimer);
        this._disconnectTimer = setTimeout(() => {
          if (!this._closed && this._pc?.connectionState === "disconnected") {
            this._onStopped("peer connection disconnected");
          }
        }, 5000);
      } else if (st === "connected") {
        clearTimeout(this._disconnectTimer);
      }
    };

    await this._pc.setRemoteDescription(offer);
    for (const cand of this._earlyCandidates.splice(0)) {
      this._pc
        .addIceCandidate(cand)
        .catch((e) => console.debug("adc-webrtc-card: early ICE add failed", e));
    }
    const answer = await this._pc.createAnswer();
    await this._pc.setLocalDescription(answer);

    await this._tx({
      janus: "message",
      session_id: this._sessionId,
      handle_id: this._handleId,
      body: { request: "start" },
      jsep: { type: "answer", sdp: answer.sdp },
    });
  }

  close() {
    if (this._closed) return;
    this._closed = true;
    clearTimeout(this._disconnectTimer);
    if (this._keepalive) {
      clearInterval(this._keepalive);
      this._keepalive = null;
    }
    // Mirror pyadc's teardown order so Janus releases the RTSP ingest before
    // the session goes away (prevents a stalled re-ingest on reconnect):
    // destroy mountpoint -> destroy session -> close WS -> close PC.
    try {
      if (this._ws && this._ws.readyState === WebSocket.OPEN) {
        if (this._sessionId && this._handleId && this._streamId) {
          this._fire({
            janus: "message",
            session_id: this._sessionId,
            handle_id: this._handleId,
            body: { request: "destroy", id: this._streamId },
          });
        }
        if (this._sessionId) {
          this._fire({ janus: "destroy", session_id: this._sessionId });
        }
      }
    } catch (e) {
      /* best-effort teardown */
    }
    for (const [, p] of this._pending) {
      clearTimeout(p.timer);
      p.reject(new Error("closed"));
    }
    this._pending.clear();
    try {
      this._ws?.close();
    } catch (e) {
      /* ignore */
    }
    this._ws = null;
    try {
      this._pc?.close();
    } catch (e) {
      /* ignore */
    }
    this._pc = null;
  }

  _openWebSocket() {
    return new Promise((resolve, reject) => {
      let settled = false;
      const ws = new WebSocket(this._info.gateway_url, "janus-protocol");
      ws.onopen = () => {
        settled = true;
        this._ws = ws;
        resolve();
      };
      ws.onerror = () => {
        if (!settled) {
          settled = true;
          reject(new Error("Janus WebSocket connection failed"));
        }
      };
      ws.onclose = () => {
        if (!settled) {
          settled = true;
          reject(new Error("Janus WebSocket closed during connect"));
        } else if (!this._closed) {
          this._onStopped("Janus WebSocket closed");
        }
      };
      ws.onmessage = (ev) => this._onMessage(ev);
    });
  }

  _onMessage(ev) {
    let msg;
    try {
      msg = JSON.parse(ev.data);
    } catch (e) {
      return;
    }

    // Trickle candidates from Janus -> browser PC.
    if (msg.janus === "trickle") {
      const cand = msg.candidate ?? {};
      if (!cand.completed && cand.candidate) {
        if (this._pc && this._pc.remoteDescription) {
          this._pc
            .addIceCandidate(cand)
            .catch((e) => console.debug("adc-webrtc-card: ICE add failed", e));
        } else {
          this._earlyCandidates.push(cand);
        }
      }
      return;
    }

    // Transaction responses. "ack" just acknowledges receipt — the real
    // response ("success"/"event"/"error") follows with the same transaction.
    const tx = msg.transaction;
    if (tx && this._pending.has(tx)) {
      if (msg.janus === "ack") return;
      const p = this._pending.get(tx);
      if (msg.janus === "error") {
        this._pending.delete(tx);
        clearTimeout(p.timer);
        p.reject(new Error(msg.error?.reason ?? "Janus error"));
        return;
      }
      // For `watch` the offer may arrive on a later event than the first
      // (e.g. a "preparing" status event) — keep waiting until a jsep shows.
      if (p.wantJsep && !msg.jsep) return;
      this._pending.delete(tx);
      clearTimeout(p.timer);
      p.resolve(msg);
      return;
    }

    // Untransacted events: hangups and plugin "stopped" statuses.
    if (msg.janus === "hangup" && !this._closed) {
      this._onStopped(msg.reason ?? "hangup");
      return;
    }
    const status = msg.plugindata?.data?.result?.status;
    if (status === "stopped" && !this._closed) {
      this._onStopped("stream stopped");
    }
  }

  _fire(msg) {
    if (!this._ws || this._ws.readyState !== WebSocket.OPEN) return;
    this._ws.send(
      JSON.stringify({
        ...msg,
        transaction: Math.random().toString(16).slice(2, 10),
        token: this._info.token,
      })
    );
  }

  _tx(msg, { wantJsep = false } = {}) {
    return new Promise((resolve, reject) => {
      if (!this._ws || this._ws.readyState !== WebSocket.OPEN) {
        reject(new Error("Janus WebSocket not open"));
        return;
      }
      const tx = Math.random().toString(16).slice(2, 10);
      const timer = setTimeout(() => {
        this._pending.delete(tx);
        reject(new Error(`Janus request timed out: ${msg.janus}`));
      }, TX_TIMEOUT_MS);
      this._pending.set(tx, { resolve, reject, wantJsep, timer });
      this._ws.send(JSON.stringify({ ...msg, transaction: tx, token: this._info.token }));
    });
  }
}

class AdcWebrtcCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = null;
    this._stream = null; // JanusStream
    this._state = "idle"; // idle | connecting | playing | error
    this._error = "";
    this._statusMsg = "";
    this._triedFallback = false;
    this._frameTimer = null;
    this._rendered = false;
    this._retryCount = 0;
    this._retryTimer = null;
    this._userStopped = false;
  }

  setConfig(config) {
    if (!config.entity || !config.entity.startsWith("camera.")) {
      throw new Error("adc-webrtc-card: `entity` must be a camera entity");
    }
    this._config = config;
    this._rendered = false;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    // Keep the poster fresh while idle (entity_picture rotates its token).
    if (this._state === "idle" || this._state === "error") this._updatePoster();
  }

  getCardSize() {
    return 5;
  }

  static getStubConfig(hass) {
    const cam = Object.keys(hass?.states ?? {}).find((e) => e.startsWith("camera."));
    return { entity: cam ?? "camera.example" };
  }

  connectedCallback() {
    this._render();
    this._onFsChange = () => this._syncFullscreenBadge();
    document.addEventListener("fullscreenchange", this._onFsChange);
    document.addEventListener("webkitfullscreenchange", this._onFsChange);
    if (this._config?.autoplay && this._state === "idle") this._play();
  }

  disconnectedCallback() {
    document.removeEventListener("fullscreenchange", this._onFsChange);
    document.removeEventListener("webkitfullscreenchange", this._onFsChange);
    this._stop("card removed");
  }

  // ---------------------------------------------------------------- UI --

  _render() {
    if (!this.shadowRoot || !this._config) return;
    if (!this._rendered) {
      this.shadowRoot.innerHTML = `
        <style>
          ha-card { overflow: hidden; }
          .frame {
            position: relative;
            width: 100%;
            aspect-ratio: 16 / 9;
            background: #000;
          }
          .frame:fullscreen,
          .frame:-webkit-full-screen {
            aspect-ratio: auto;
            width: 100%;
            height: 100%;
          }
          img, video {
            position: absolute;
            inset: 0;
            width: 100%;
            height: 100%;
            object-fit: contain;
            background: #000;
          }
          .overlay {
            position: absolute;
            inset: 0;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-direction: column;
            gap: 8px;
            color: #fff;
            background: rgba(0, 0, 0, 0.25);
            cursor: pointer;
            font: 14px sans-serif;
            text-align: center;
            padding: 8px;
          }
          .overlay .icon { font-size: 48px; line-height: 1; }
          .badges {
            position: absolute;
            top: 8px;
            right: 8px;
            display: none;
            gap: 8px;
          }
          .badge {
            padding: 4px 10px;
            border-radius: 12px;
            background: rgba(0, 0, 0, 0.6);
            color: #fff;
            font: 13px/1.4 sans-serif;
            cursor: pointer;
            user-select: none;
            -webkit-user-select: none;
          }
          .spin {
            width: 36px; height: 36px;
            border: 4px solid rgba(255,255,255,.3);
            border-top-color: #fff;
            border-radius: 50%;
            animation: s 1s linear infinite;
          }
          @keyframes s { to { transform: rotate(360deg); } }
        </style>
        <ha-card>
          <div class="frame" id="frame">
            <img id="poster" alt="" />
            <video id="video" autoplay playsinline muted style="display:none"></video>
            <div class="overlay" id="overlay">
              <div class="icon">▶</div>
              <div id="msg">Live view</div>
            </div>
            <div class="badges" id="badges">
              <div class="badge" id="fs" title="Full screen">⛶</div>
              <div class="badge" id="stop">■ Stop</div>
            </div>
          </div>
        </ha-card>
      `;
      this.shadowRoot.getElementById("overlay").onclick = () => {
        if (this._state === "idle" || this._state === "error") this._play();
      };
      this.shadowRoot.getElementById("stop").onclick = () => this._stop("user");
      this.shadowRoot.getElementById("fs").onclick = () => this._toggleFullscreen();
      this.shadowRoot.getElementById("video").ondblclick = () =>
        this._toggleFullscreen();
      this._rendered = true;
    }
    this._updatePoster();
    this._syncUi();
  }

  _updatePoster() {
    const poster = this.shadowRoot?.getElementById("poster");
    if (!poster || !this._hass || !this._config) return;
    const st = this._hass.states[this._config.entity];
    const pic = st?.attributes?.entity_picture;
    if (pic && poster.getAttribute("src") !== pic) poster.src = pic;
  }

  _syncUi() {
    const $ = (id) => this.shadowRoot.getElementById(id);
    if (!this._rendered) return;
    const overlay = $("overlay");
    const msg = $("msg");
    const icon = overlay.querySelector(".icon");
    const video = $("video");
    const poster = $("poster");
    const badges = $("badges");

    const playing = this._state === "playing";
    video.style.display = playing ? "" : "none";
    poster.style.display = playing ? "none" : "";
    badges.style.display = playing ? "flex" : "none";
    overlay.style.display = playing ? "none" : "flex";

    if (this._state === "idle") {
      icon.textContent = "▶";
      icon.classList.remove("spin");
      msg.textContent = "Live view";
    } else if (this._state === "connecting") {
      icon.textContent = "";
      icon.classList.add("spin");
      msg.textContent = this._statusMsg || "Connecting…";
    } else if (this._state === "error") {
      icon.textContent = "▶";
      icon.classList.remove("spin");
      msg.textContent = `Stream error: ${this._error} — tap to retry`;
    }
  }

  // ------------------------------------------------------------ fullscreen --

  _isFullscreen() {
    return !!(this.shadowRoot.fullscreenElement || document.fullscreenElement);
  }

  /**
   * Fullscreen the frame (so the reconnect overlay and badges stay visible)
   * via the standard API where available. iOS Safari / the HA iOS app don't
   * support element fullscreen — fall back to the video element's native
   * fullscreen player (webkitEnterFullscreen), which only works mid-playback.
   */
  _toggleFullscreen() {
    const frame = this.shadowRoot.getElementById("frame");
    const video = this.shadowRoot.getElementById("video");
    if (this._isFullscreen()) {
      (document.exitFullscreen ?? document.webkitExitFullscreen)?.call(document);
      return;
    }
    if (frame.requestFullscreen) {
      frame
        .requestFullscreen()
        .catch(() => video.webkitEnterFullscreen?.());
    } else if (frame.webkitRequestFullscreen) {
      frame.webkitRequestFullscreen();
    } else if (video.webkitEnterFullscreen) {
      video.webkitEnterFullscreen();
    }
  }

  _syncFullscreenBadge() {
    const fs = this.shadowRoot?.getElementById("fs");
    if (!fs) return;
    const active = this._isFullscreen();
    fs.textContent = active ? "⛶ Exit" : "⛶";
    fs.title = active ? "Exit full screen" : "Full screen";
  }

  _setState(state, error = "") {
    this._state = state;
    this._error = error;
    if (state !== "connecting") this._statusMsg = "";
    this._syncUi();
  }

  // ------------------------------------------------------------- flow --

  async _play() {
    this._userStopped = false;
    this._retryCount = 0;
    this._triedFallback = false;
    this._clearRetryTimer();
    await this._connect(this._config.hd ?? true);
  }

  async _connect(hd) {
    if (!this._hass) return;
    if (this._state !== "connecting") this._setState("connecting");
    try {
      const info = await this._hass.connection.sendMessagePromise({
        type: "alarmdotcom_ha/camera_stream_info",
        entity_id: this._config.entity,
        hd,
      });
      const stream = new JanusStream(
        info,
        (media) => this._attachMedia(media),
        (reason) => this._onStreamStopped(reason)
      );
      this._stream = stream;
      await stream.connect();
      // If no decodable video arrives, retry once on the other relay —
      // some ADC cameras only deliver via HD, others only via SD.
      this._armFrameTimeout(hd);
    } catch (e) {
      console.warn("adc-webrtc-card: connect failed", e);
      this._teardown();
      if (!this._triedFallback) {
        this._triedFallback = true;
        this._connect(!hd);
        return;
      }
      this._failedAttempt(e.message ?? String(e));
    }
  }

  /** Both relays failed for this attempt — retry (autoplay / mid-stream
   *  reconnect cycles) or surface the error (a user-initiated first play). */
  _failedAttempt(reason) {
    if (this._config.autoplay || this._retryCount > 0) {
      this._scheduleReconnect(reason);
    } else {
      this._setState("error", reason);
    }
  }

  _scheduleReconnect(reason) {
    if (this._userStopped || !this.isConnected || this._retryTimer) return;
    const autoplay = !!this._config.autoplay;
    if (!autoplay && this._retryCount >= RECONNECT_MAX_ATTEMPTS) {
      this._setState("error", `${reason} (gave up after ${this._retryCount} retries)`);
      return;
    }
    const delay = Math.min(
      RECONNECT_BASE_MS * 2 ** this._retryCount,
      RECONNECT_MAX_DELAY_MS
    );
    this._retryCount += 1;
    console.debug(
      `adc-webrtc-card: reconnect #${this._retryCount} in ${delay}ms (${reason})`
    );
    this._statusMsg = autoplay
      ? "Reconnecting…"
      : `Reconnecting… (${this._retryCount}/${RECONNECT_MAX_ATTEMPTS})`;
    this._setState("connecting");
    this._retryTimer = setTimeout(() => {
      this._retryTimer = null;
      if (this._userStopped || !this.isConnected) return;
      this._triedFallback = false;
      this._connect(this._config.hd ?? true);
    }, delay);
  }

  _clearRetryTimer() {
    if (this._retryTimer) {
      clearTimeout(this._retryTimer);
      this._retryTimer = null;
    }
  }

  _attachMedia(media) {
    const video = this.shadowRoot.getElementById("video");
    video.srcObject = media;
    const onFrames = () => {
      video.removeEventListener("loadeddata", onFrames);
      this._clearFrameTimeout();
      this._retryCount = 0; // healthy again — reset the backoff
      this._setState("playing");
    };
    video.addEventListener("loadeddata", onFrames);
  }

  _armFrameTimeout(usedHd) {
    this._clearFrameTimeout();
    this._frameTimer = setTimeout(() => {
      if (this._state === "playing") return;
      if (!this._triedFallback) {
        console.debug(
          `adc-webrtc-card: no video from ${usedHd ? "HD" : "SD"} relay, ` +
            `retrying ${usedHd ? "SD" : "HD"}`
        );
        this._triedFallback = true;
        this._teardown();
        this._connect(!usedHd);
      } else {
        this._teardown();
        this._failedAttempt("no video from either relay");
      }
    }, FIRST_FRAME_TIMEOUT_MS);
  }

  _clearFrameTimeout() {
    if (this._frameTimer) {
      clearTimeout(this._frameTimer);
      this._frameTimer = null;
    }
  }

  _onStreamStopped(reason) {
    console.debug("adc-webrtc-card: stream stopped:", reason);
    if (this._userStopped) return;
    this._teardown();
    // A drop mid-stream (Janus hangup, ICE failure, token expiry) or during
    // setup — reconnect with backoff; each attempt mints fresh credentials.
    this._scheduleReconnect(reason);
  }

  _teardown() {
    this._clearFrameTimeout();
    const video = this.shadowRoot?.getElementById("video");
    if (video) video.srcObject = null;
    this._stream?.close();
    this._stream = null;
  }

  _stop(reason) {
    this._userStopped = true;
    this._clearRetryTimer();
    this._teardown();
    if (this._isFullscreen()) {
      (document.exitFullscreen ?? document.webkitExitFullscreen)?.call(document);
    }
    this._setState("idle");
  }
}

if (!customElements.get("adc-webrtc-card")) {
  customElements.define("adc-webrtc-card", AdcWebrtcCard);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "adc-webrtc-card")) {
  window.customCards.push({
    type: "adc-webrtc-card",
    name: "Alarm.com WebRTC Camera",
    description:
      "Live WebRTC view for alarmdotcom_ha cameras — streams directly from " +
      "the browser to Alarm.com (no aiortc needed).",
  });
}
