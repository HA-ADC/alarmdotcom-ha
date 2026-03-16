# alarmdotcom_ha — Home Assistant Integration

Unofficial Home Assistant custom integration for Alarm.com, built on [`pyadc`](https://github.com/HA-ADC/pyadc). Built by an alarm.com engineer to utilize websockets and official api endpoints. This is an unofficial Alarm.com integration and **should not** be used as a replacement for home security. An Alarm.com subscription is also required to utilize this pacakge.

## Safety Warnings
This integration is intended for casual use with Home Assistant and not as a replacement too keep you safe.

- This integration communicates with Alarm.com over a channel that can be broken or changed at any time.
- It may take several minutes for this integration to receive a status update from Alarm.com's servers.
- Your automations may be buggy.
- This code may be buggy. It's written by volunteers in their free time and testing is spotty.
- You should use Alarm.com's official apps, devices, and services for notifications of all kinds related to safety, break-ins, property damage (e.g.: freeze sensors), etc.

Where possible, use local control for smart home devices that are natively supported by Home Assistant (lights, garage door openers, etc.). Locally controlled devices will continue to work during internet outages whereas this integraiton will not.

## Features

- Real-time state updates via WebSocket
- Config flow with username/password, OTP/2FA, and device trust
- Utilizing official api endpoints and websocket messages to help ensure reliability
- Large amount of device support

## Supported Devices

Below is a table of the currently supported device types. Under the communiy tested column I have included devices that have been personally tested by the community. **Just because your device of a given type is not explicitly listed, does not mean it isn't supported (Ex IQ4 is listed but you have an IQ2)** If you have a device not on the list and it is working, open an issue or pull request to get it added. Please supply proof (a short video) that the device is working. With your support we can get this list from 🟧 to ✅.

✅ Tested - either on physical device or is able to see states. For devices with actions, the actions have also been tested. <br/>
🟧  Supported in Theory - Code is in place but do not have a device to test with

<!-- Keep these in Alphabetical order -->
| Device | Community Tested | HA Platform | Transport | Notes |
|--------|------------------|-------------|-----------|-------|
| Battery levels | ✅ <br/> Yale Assure Lock | `sensor` (%) | WebSocket | Per-device diagnostic |
| CO detectors | 🟧 <br/> | `binary_sensor` (CO) | WebSocket | |
| Color-temp lights | ✅ <br/> Zipato RGBW Bulb | `light` | WebSocket | Warm/cool white |
| Contact sensors (door/window) | ✅ <br/> QS1135-840 | `binary_sensor` (DOOR) | WebSocket | |
| Dimmable switches | ✅ <br/> | `switch` | WebSocket | |
| Garage doors | ✅  <br/> | `cover` (GARAGE) | WebSocket | |
| Gas sensors | 🟧 <br/> | `binary_sensor` (GAS) | WebSocket | This is I assume a propane sensor |
| Gates | 🟧 <br/> | `cover` (GATE) | WebSocket | |
| Glassbreak sensors | ✅ <br/> States are reporting on IQ4. Have not been able to trigger a glass break sound to test that state. | `binary_sensor` (SOUND) | WebSocket | |
| Image sensors | 🟧 <br/> | `image` | Poll (30 min) | |
| Lights (on/off, dimmable) | ✅ <br/> Zipato RGBW Bulb | `light` | WebSocket | These would be physical light bulbs. |
| Locks | ✅ <br/> Yale Assure series locks | `lock` | WebSocket | |
| Low battery state | ✅ | `binary_sensor` (BATTERY) | WebSocket | Per-device diagnostic |
| Malfunction state | ✅ | `binary_sensor` (PROBLEM) | WebSocket | Per-device diagnostic |
| Motion sensors | ✅ <br/> No tested physical devices, but states are reporting | `binary_sensor` (MOTION) | WebSocket | |
| On/off switches | ✅ <br/> Jasco 46562 | `switch` | WebSocket | I believe outlet switches would be under this category too |
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
    ├── image.py              # Image sensors — polls every 30 min for image URL
    ├── light.py              # on/off, dimming, RGB, color temp
    ├── lock.py
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
