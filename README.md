# alarmdotcom_ha — Home Assistant Integration

Unofficial Home Assistant custom integration for Alarm.com, built on [`pyadc`](https://github.com/HA-ADC/pyadc). Built by an alarm.com engineer to utilize websockets and official api endpoints. This is an unofficial Alarm.com integration and **should not** be used as a replacement for home security. An Alarm.com subscription is also required to utilize this package.

## Safety Warnings
This integration is intended for casual use with Home Assistant and not as a replacement to keep you safe.

- This integration communicates with Alarm.com over a channel that can be broken or changed at any time.
- It may take several minutes for this integration to receive a status update from Alarm.com's servers.
- Your automations may be buggy.
- This code may be buggy. It's written by volunteers in their free time and testing is spotty.
- You should use Alarm.com's official apps, devices, and services for notifications of all kinds related to safety, break-ins, property damage (e.g.: freeze sensors), etc.

Where possible, use local control for smart home devices that are natively supported by Home Assistant (lights, garage door openers, etc.). Locally controlled devices will continue to work during internet outages whereas this integration will not.

## Features

- Real-time state updates via WebSocket
- Config flow with username/password, OTP/2FA, and device trust
- Utilizing official api endpoints and websocket messages to help ensure reliability
- Large amount of device support
- Camera support — still snapshots, WebRTC live streaming (bundled custom card, works on HA OS), and per-camera person / vehicle / animal / package detection sensors
- Optimistic state updates — UI reflects changes instantly before server confirmation
- Seamless token rotation — persisted across restarts for fast re-authentication
- Custom arming services (`arm_away_options`, `arm_stay_options`, `arm_night_options`) with silent arming, force bypass, and no-entry-delay options

## Supported Devices

Below is a table of the currently supported device types. Under the communiy tested column I have included devices that have been personally tested by the community. **Just because your device of a given type is not explicitly listed, does not mean it isn't supported (Ex IQ4 is listed but you have an IQ2)** If you have a device not on the list and it is working, open an issue or pull request to get it added. Please supply proof (a short video) that the device is working. With your support we can get this list from 🟧 to ✅.

✅ Tested - either on physical device or is able to see states. For devices with actions, the actions have also been tested. <br/>
🟧  Supported in Theory - Code is in place but do not have a device to test with

<!-- Keep these in Alphabetical order -->
| Device | Community Tested | HA Platform | Transport | Notes |
|--------|------------------|-------------|-----------|-------|
| Battery levels | ✅ <br/> Yale Assure Lock | `sensor` (%) | WebSocket | Per-device diagnostic |
| Camera object detection (person / vehicle / animal / package) | ✅ <br/> | `binary_sensor` (MOTION) | WebSocket | Four momentary sensors per camera; auto-clear ~10s after the last detection event. Requires video analytics on the camera/plan. |
| Cameras | ✅ <br/> | `camera` | REST + WebRTC | Still snapshots always work. Live view works out of the box via the bundled **`adc-webrtc-card`** (the browser streams directly from ADC's Janus gateway — nothing to install). HA's *native* stream view (more-info dialog, picture-glance live) additionally needs the optional `aiortc` package, which cannot be installed on HA OS. See "Camera live view" below. |
| CO detectors | 🟧 <br/> | `binary_sensor` (CO) | WebSocket | |
| Color-temp lights | ✅ <br/> Zipato RGBW Bulb | `light` | WebSocket | Warm/cool white |
| Contact sensors (door/window) | ✅ <br/> QS1135-840 | `binary_sensor` (DOOR) | WebSocket | |
| Dimmable switches | ✅ <br/> | `switch` | WebSocket | |
| Garage doors | ✅  <br/> | `cover` (GARAGE) | WebSocket | |
| Gas sensors | 🟧 <br/> | `binary_sensor` (GAS) | WebSocket | This is I assume a propane sensor |
| Gates | 🟧 <br/> | `cover` (GATE) | WebSocket | |
| Glassbreak sensors | ✅ <br/> States are reporting on IQ4. Have not been able to trigger a glass break sound to test that state. | `binary_sensor` (SOUND) | WebSocket | |
| Image sensors | 🟧 <br/> | `image` + `button` | Poll (30 min) | "Peek In" button requests an on-demand capture; the image refreshes within seconds of the upload. |
| Lights (on/off, dimmable) | ✅ <br/> Zipato RGBW Bulb | `light` | WebSocket | These would be physical light bulbs. |
| Locks | ✅ <br/> Yale Assure series locks | `lock` | WebSocket | |
| Low battery state | ✅ | `binary_sensor` (BATTERY) | WebSocket | Per-device diagnostic |
| Malfunction state | ✅ | `binary_sensor` (PROBLEM) | WebSocket | Per-device diagnostic |
| Motion sensors | ✅ <br/> No tested physical devices, but states are reporting | `binary_sensor` (MOTION) | WebSocket | |
| On/off switches | ✅ <br/> Jasco 46562 | `switch` | WebSocket | I believe outlet switches would be under this category too |
| Panel / PIR image cameras | ✅ <br/> IQ4 panel camera | `image` + `button` | Poll (30 min) | Latest capture shown at startup; "Peek In" button takes a fresh capture and the image refreshes within seconds. Covers Qolsys/Honeywell/GC-Next panel cameras and Climax/DSC/PowerG PIR cameras. |
| RGB lights | ✅ <br/> Zipato RGBW Bulb | `light` | WebSocket | Full RGB color control |
| Security Panel | ✅ <br/> IQ4 | `alarm_control_panel` | WebSocket | Arm/Away/Stay/Night/Disarm |
| Smoke/heat detectors | 🟧 <br/> | `binary_sensor` (SMOKE) | WebSocket | |
| Thermostats | ✅  <br/> Dreamstat Gen 2 | `climate` | WebSocket | Reports in Home assistant units. For example, your ADC system is in metric but your HA system is in imperial, the device will output imperial. |
| Temperature Sensors | 🟧 <br/> Physical device tested, however support seemed intermittent | `sensor` (temperature) | WebSocket | Reports in Home Assistant units (respects HA's unit system preference). |
| Water meters | ✅ <br/> ADC-SHM-100 | `sensor` (gal/L) | Poll (1 hr) | Usage today + Daily Average |
| Water valves | ✅ <br/> Econet Water Valve | `valve` | WebSocket | |
| Water/leak sensors | ✅  <br/> | `binary_sensor` (MOISTURE) | WebSocket | |

## Devices Not Supported (Blacklisted)

| Device | Reason |
|--------|--------|
| Location Devices (your phone) | Privacy-sensitive; geolocation is better handled by HA's native `device_tracker` platform via the mobile companion app. Would duplicate functionality with worse UX. |
| Access Card Readers | Commercial-only ADC product. Consumer ADC accounts cannot access these. Access control management is also out of scope for a home automation integration. |
| Power Meters | Requires deep HA Energy dashboard integration. ADC's power meter data is also available via dedicated energy integrations with better support. |
| IQ Router | Network device management is out of scope for home automation. |
| ADC Scenes | Conflicts with HA's own automation and scene system. Using ADC scenes from HA would create confusing UX. |

## Planned Future Devices

Submit issue requests with the device type and model and we will try to get it added as soon as we can! If possible please include logs as to what endpoint the device is hitting.

| Device | HA Platform | Priority | Notes |
|--------|-------------|----------|-------|
| Shades | Unknown | Medium | Looking for an account with working shades to test with

---

## Installation

### Manual
1. Copy `custom_components/alarmdotcom_ha/` into your HA config directory:
   ```
   config/custom_components/alarmdotcom_ha/
   ```
2. Restart Home Assistant.
3. Go to **Settings → Devices & Services → Add Integration** and search for **Alarm.com (pyadc)**.

### HACS
Add this repository as a custom HACS integration source, then install `alarmdotcom_ha`.

## Requirements

- Home Assistant 2024.11+ (the camera platform uses HA's async WebRTC provider API)
- `pyadc` — pinned by git tag in `manifest.json` and installed by HA automatically
- Optional: `aiortc` (`pip install "pyadc[webrtc]"` into HA's venv) for HA's *native* camera stream view. **Not installable on HA OS** (aiortc pins `av<17`, HA core ships `av>=17`) — use the bundled `adc-webrtc-card` there instead, which needs no extra packages.

---

## Setup

The integration uses a UI config flow:

1. **Credentials** — enter your Alarm.com username and password.
2. **Two-factor auth** — if your account has MFA enabled, enter the code sent via SMS or email.
3. **Trust device** — optionally trust this device to skip MFA on future logins.
4. **Re-auth** — if your Alarm.com **password changes** (or the account is locked), initialization will fail with `AuthenticationFailed` and HA will prompt you to enter new credentials from the integration page.

> **Note on automatic recovery:** Most session disruptions are handled without any user interaction:
> - *WebSocket JWT expiry* (close code 1008) — `pyadc` automatically re-runs the login flow using stored credentials and reconnects.
> - *Repeated connection failures* (DEAD state after 25 attempts) — the integration reloads itself automatically.
> The manual reauth prompt only appears if your actual Alarm.com **password** is no longer valid.

The `mfa_cookie` is stored securely in the HA config entry and reused on restart.

---

## Camera live view

There are two live-view paths; snapshots always work regardless.

### Bundled WebRTC card (recommended — works everywhere, including HA OS)

The integration ships `adc-webrtc-card` and **auto-loads it on every
dashboard** — no HACS plugin, no manual Lovelace resource, nothing extra to
install. The card speaks the Janus protocol directly from your browser to
Alarm.com (the same flow ADC's own web app uses), so no extra Python packages
are needed and no media passes through Home Assistant.

**Adding the card:** edit a dashboard → **Add card** → search for
"Alarm.com WebRTC Camera" (or add it by YAML):

```yaml
type: custom:adc-webrtc-card
entity: camera.front_doorbell
```

#### The two modes

| | Tap-to-play (default) | Kiosk (`autoplay: true`) |
|---|---|---|
| On page load | Shows the camera snapshot with a ▶ button | Starts streaming immediately |
| Stream drops mid-play | Reconnects automatically | Reconnects automatically |
| Sustained outage (internet/ADC down) | Gives up after 5 straight failed attempts → "tap to retry" | Never gives up — keeps retrying every ≤30 s |
| Best for | Normal dashboards you browse | Wall tablets / always-on displays |

```yaml
type: custom:adc-webrtc-card
entity: camera.front_doorbell
autoplay: true
```

In both modes a dropped stream (camera hiccup, network blip, ADC's periodic
relay-session expiry, hourly token rollover) shows "Reconnecting…" and
resumes on its own — the retry counter resets every time video comes back,
so a playing stream survives ADC's routine session kills indefinitely. Each
reconnect fetches fresh credentials, with exponential backoff
(2 s → 4 s → … → 30 s) between failed attempts. Pressing **stop** always
stays stopped.

The card also falls back automatically between ADC's HD and SD relay
endpoints — some camera models only deliver video on one of them.

> **Notes:**
> - The browser must be able to reach `*.alarm.com`. Live view works when
>   accessing HA remotely too — media flows browser ↔ ADC, not through your
>   HA instance.
> - After upgrading the integration, hard-refresh the dashboard once
>   (Ctrl/Cmd+Shift+R) if a card misbehaves — the card URL is cache-busted
>   per release, but an already-open tab keeps the old module until reload.

### Native HA stream (optional, not on HA OS)

With the `aiortc` package installed in HA's venv (`pip install
"pyadc[webrtc]"`), camera entities also advertise HA's native WebRTC stream —
live view in the more-info dialog and standard picture cards. aiortc pins
`av<17` while HA core ships `av>=17`, so this only works on installs where
you control the venv (e.g. HA Core / devcontainer), **not HA OS**. Without
aiortc the entity is snapshot-only and the bundled card is the live path.

---

## Tips

### Door vs. window contacts

Alarm.com's API models door and window contacts as the same physical
"door/window contact" device type and does **not** report which is which, so
every contact sensor defaults to the **door** device class. If you want a
contact treated as a **window** (for example so Home Assistant's purpose-based
triggers and sorting group it correctly):

1. Open the sensor entity → **Settings** (gear icon).
2. Use the **"Show as"** selector and choose **Window** (or any class you prefer).

Home Assistant stores this override in the entity registry and it takes
precedence over the integration's default — no configuration in the integration
is required, and the choice survives restarts.

---

## Architecture

```
alarmdotcom_ha/
└── custom_components/alarmdotcom_ha/
    ├── __init__.py           # Entry setup/unload, forwards to all platforms
    ├── hub.py                # AlarmHub — owns session, AlarmBridge, WS state tracking
    ├── entity.py             # AdcEntity[T] — base entity, EventBroker subscription, availability
    ├── config_flow.py        # ConfigFlow: user → two_factor → trust_device → reauth
    ├── const.py              # DOMAIN, config key constants, WATER_METER_DEVICE_TYPE
    ├── manifest.json         # HA integration metadata (domain, requirements, iot_class)
    ├── services.yaml         # arm_away/stay/night_options custom services
    ├── strings.json          # Config flow UI string keys
    ├── translations/en.json  # English UI strings
    ├── websocket_api.py      # WS command serving stream credentials to the card
    ├── www/adc-webrtc-card.js # Bundled card: browser ↔ ADC Janus live view (no aiortc)
    ├── alarm_control_panel.py
    ├── binary_sensor.py      # Sensors + per-camera person/vehicle/animal/package detection
    ├── camera.py             # Snapshots + native WebRTC live view (needs optional aiortc)
    ├── climate.py            # Full feature set: FAN_ONLY, humidity, presets, hvac_action
    ├── cover.py              # GarageDoor (GARAGE) + Gate (GATE device class)
    ├── image.py              # Image sensors + panel/PIR cameras — 30 min poll, primed at startup
    ├── light.py              # on/off, dimming, RGB, color temp
    ├── lock.py
    ├── button.py             # Image-sensor peek-in, debug, and clear-faults buttons
    ├── switch.py             # On/off light switches (DeviceType 17)
    ├── sensor.py             # Battery %, temperature sensors, thermostat temp/humidity, water usage
    └── valve.py
```

### Event flow

```
pyadc WebSocket frame
  └── EventBroker.publish(ResourceEventMessage)
        └── AdcEntity._handle_update()        (subscribed per device_id)
              └── self.async_write_ha_state()  (schedules HA state machine write)
```

### Connection lifecycle

```
AlarmHub.initialize()
  └── AlarmBridge.initialize()     REST login + fetch all devices
  └── EventBroker.subscribe(CONNECTION_EVENT, _handle_connection_event)
  └── AlarmBridge.start_websocket()

_handle_connection_event(CONNECTED)   → hub.connected = True  → entities available
_handle_connection_event(DISCONNECTED) → hub.connected = False → entities unavailable
_handle_connection_event(DEAD)         → hub.connected = False → schedule config entry reload
  └── hass.config_entries.async_reload()  → full re-auth via config flow
```

---

## Adding a New HA Platform

1. **Ensure pyadc has the model and controller** — see `pyadc/README.md` → "Adding a New Device Type".

2. **Create the platform file** `custom_components/alarmdotcom_ha/my_platform.py`:
   ```python
   from homeassistant.components.my_platform import MyEntity
   from pyadc.models.my_device import MyDevice
   from .const import DATA_BRIDGE, DOMAIN
   from .entity import AdcEntity
   from .hub import AlarmHub

   async def async_setup_entry(hass, entry, async_add_entities):
       hub: AlarmHub = hass.data[DOMAIN][entry.entry_id][DATA_BRIDGE]
       async_add_entities(
           AdcMyEntity(hub, device)
           for device in hub.bridge.my_devices.devices
       )

   class AdcMyEntity(AdcEntity[MyDevice], MyEntity):
       """Alarm.com my-device entity."""

       @property
       def some_state(self):
           return self._device.state
   ```
   - Inherit from `AdcEntity[YourModel]` and the relevant HA entity class.
   - `should_poll = False` is inherited from `AdcEntity` — do not override unless intentionally polling.
   - `available` is inherited — returns `False` when WS is down or device is disabled.
   - `async_added_to_hass` / `async_will_remove_from_hass` are inherited — do not re-implement unless you need extra subscriptions.

3. **Register the platform** in `__init__.py`:
   ```python
   from homeassistant.const import Platform
   PLATFORMS = [
       ...,
       Platform.MY_PLATFORM,
   ]
   ```

4. **Verify**: restart HA, add integration, confirm entities appear in the correct domain.

---

## Development Setup

```bash
# From the workspace root
cd pyadc
pip install -e ".[dev]"   # installs pyadc in editable mode

# To test the HA integration manually:
# Copy alarmdotcom_ha/custom_components/alarmdotcom_ha/ to your HA dev instance
# and enable debug logging:
```

In your HA `configuration.yaml`:
```yaml
logger:
  default: warning
  logs:
    custom_components.alarmdotcom_ha: debug
    pyadc: debug
```

---

## Updating the pyadc Dependency

`alarmdotcom_ha` depends on `pyadc` via a pinned git tag in `manifest.json`. When a new `pyadc` version is released:

### 1. Update `manifest.json`

```json
"requirements": ["pyadc @ git+https://github.com/HA-ADC/pyadc@vX.Y.Z"]
```

Replace `vX.Y.Z` with the new tag. The tag must exist in the `pyadc` repository before HA tries to install it.

### 2. Commit and push

```bash
git add custom_components/alarmdotcom_ha/manifest.json
git commit -m "chore: update pyadc dependency to vX.Y.Z"
git push origin main
```

### 3. Test in production HA

On a real HA instance (with internet access), reload or reinstall the integration — HA will pip-install the new `pyadc` version from GitHub automatically.

### Dev environment note

The devcontainer has **no internet access** — HA cannot fetch from the git URL. For local dev, run `deploy.sh` from the workspace root — it finds the running HA devcontainer, syncs both the pyadc source and this custom component into it, and restarts HA:

```bash
./deploy.sh
```

The committed `manifest.json` always keeps the real git URL — only patch it locally.

---

## License

MIT
