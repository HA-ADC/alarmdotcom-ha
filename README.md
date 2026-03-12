# alarmdotcom_ha — Home Assistant Integration

Home Assistant custom integration for Alarm.com, built on [`pyadc`](../pyadc/). All device state is **WebSocket-pushed** — no polling except image sensors.

## Features

- Real-time state updates via WebSocket (no polling)
- Config flow with username/password, OTP/2FA, and device trust
- Entities go **unavailable** automatically when the WebSocket connection drops
- WebSocket DEAD state (JWT expiry) triggers automatic HA config entry reload → re-auth
- Fixes known community bugs: `CoverDeviceClass.GATE` for gates, full climate support, battery sensors enabled

## Supported Devices

| Device | HA Platform | Transport | Notes |
|--------|-------------|-----------|-------|
| Security partitions | `alarm_control_panel` | WebSocket | Arm/Away/Stay/Night/Disarm |
| Contact sensors (door/window) | `binary_sensor` (DOOR) | WebSocket | |
| Motion sensors | `binary_sensor` (MOTION) | WebSocket | |
| Smoke/heat detectors | `binary_sensor` (SMOKE) | WebSocket | |
| CO detectors | `binary_sensor` (CO) | WebSocket | |
| Water/leak sensors | `binary_sensor` (MOISTURE) | WebSocket | Includes ADC-SHM-100-A Water Dragon |
| Gas sensors | `binary_sensor` (GAS) | WebSocket | |
| Glassbreak sensors | `binary_sensor` (SOUND) | WebSocket | |
| Locks | `lock` | WebSocket | |
| Lights (on/off, dimmable) | `light` | WebSocket | |
| RGB lights | `light` | WebSocket | Full RGB color control |
| Color-temp lights | `light` | WebSocket | Warm/cool white |
| On/off switches | `switch` | WebSocket | ADC DeviceType 17 (LightSwitchControl) |
| Thermostats | `climate` | WebSocket | All modes, humidity, fan presets |
| Garage doors | `cover` (GARAGE) | WebSocket | |
| Gates | `cover` (GATE) | WebSocket | Correct HA device class (community libraries use GARAGE incorrectly) |
| Water valves | `valve` | WebSocket | |
| Image sensors | `image` | Poll (1 min) | |
| Battery levels | `sensor` (%) | WebSocket | Per-device diagnostic |
| Malfunction state | `binary_sensor` (PROBLEM) | WebSocket | Per-device diagnostic |
| Low battery state | `binary_sensor` (BATTERY) | WebSocket | Per-device diagnostic |

## Devices Not Supported (with Reasons)

| Device | Reason |
|--------|--------|
| GPS Trackers | Privacy-sensitive; geolocation is better handled by HA's native `device_tracker` platform via the mobile companion app. Would duplicate functionality with worse UX. |
| Access Card Readers | Commercial-only ADC product. Consumer ADC accounts cannot access these. Access control management is also out of scope for a home automation integration. |
| Power Meters | Requires deep HA Energy dashboard integration. ADC's power meter data is also available via dedicated energy integrations with better support. |
| Car Monitor | Automotive telematics is out of scope for home automation. |
| IQ Router | Network device management is out of scope for home automation. |
| ADC Geo Devices | Redundant with HA's mobile companion app device tracker. |
| ADC Scenes | Conflicts with HA's own automation and scene system. Using ADC scenes from HA would create confusing UX. |
| X10 Lights | X10 protocol is obsolete (pre-2000). No modern installations to support or test against. |
| Shades | Not available in the current ADC consumer API. No live devices to test against. |

## Planned Future Devices

| Device | HA Platform | Priority | Notes |
|--------|-------------|----------|-------|
| Temperature sensors (ADC-STC-1) | `sensor` (°F/°C) | High | ADC DeviceType 41; needs live device to confirm if numeric or alarm-state only |
| Doorbell cameras | `binary_sensor` (ring) + `event` | High | Ring detection; ADC DeviceType 37 |
| Sirens | `siren` | Medium | On/off trigger; ADC DeviceType 14/29 |
| Smoke + CO combo sensors | `binary_sensor` | Medium | ADC DeviceType 53 (IQ Smoke Multi-Function) |
| Contact + shock combo sensors | `binary_sensor` | Medium | ADC DeviceType 52 (Contact Multi-Function) |
| Radon sensors | `sensor` | Low | Read-only radon level |

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

- Home Assistant 2024.1+
- `pyadc>=0.1.0` — automatically installed by HA from `manifest.json`

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

## Architecture

```
alarmdotcom_ha/
└── custom_components/alarmdotcom_ha/
    ├── __init__.py           # Entry setup/unload, forwards to all platforms
    ├── hub.py                # AlarmHub — owns session, AlarmBridge, WS state tracking
    ├── entity.py             # AdcEntity[T] — base entity, EventBroker subscription, availability
    ├── config_flow.py        # ConfigFlow: user → two_factor → trust_device → reauth
    ├── const.py              # DOMAIN, config key constants
    ├── manifest.json         # HA integration metadata (domain, requirements, iot_class)
    ├── strings.json          # Config flow UI string keys
    ├── translations/en.json  # English UI strings
    ├── alarm_control_panel.py
    ├── binary_sensor.py
    ├── climate.py            # Full feature set: FAN_ONLY, humidity, presets, hvac_action
    ├── cover.py              # GarageDoor (GARAGE) + Gate (GATE device class)
    ├── image.py              # Image sensors — polls 1/min for image URL
    ├── light.py              # on/off, dimming, RGB, color temp
    ├── lock.py
    ├── sensor.py             # Battery %, thermostat temp/humidity
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
# From repo root
cd HA_pyADC/pyadc
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

## License

MIT
