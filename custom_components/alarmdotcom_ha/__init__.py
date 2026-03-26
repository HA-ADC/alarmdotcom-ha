"""The alarmdotcom_ha integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from pyadc.exceptions import AuthenticationFailed, ServiceUnavailable

from .const import CONF_BASE_URL, CONF_MFA_COOKIE, CONF_PASSWORD, CONF_SEAMLESS_TOKEN, CONF_USERNAME, DATA_BRIDGE, DOMAIN
from .hub import AlarmHub

log = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.ALARM_CONTROL_PANEL,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CAMERA,
    Platform.CLIMATE,
    Platform.COVER,
    Platform.IMAGE,
    Platform.LIGHT,
    Platform.LOCK,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.VALVE,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up alarmdotcom_ha from a config entry."""
    # This integration is WebSocket push-based — polling does nothing useful.
    # Ensure the UI option is always off so users aren't confused.
    if not entry.pref_disable_polling:
        hass.config_entries.async_update_entry(entry, pref_disable_polling=True)

    hub = AlarmHub(
        hass,
        entry,
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        mfa_cookie=entry.data.get(CONF_MFA_COOKIE, ""),
        seamless_token=entry.data.get(CONF_SEAMLESS_TOKEN, ""),
        base_url=entry.data.get(CONF_BASE_URL, "https://www.alarm.com"),
    )

    try:
        await hub.initialize()
    except AuthenticationFailed as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except ServiceUnavailable as err:
        raise ConfigEntryNotReady(str(err)) from err
    except Exception as err:
        raise ConfigEntryNotReady(str(err)) from err

    # Persist updated credentials so future startups can skip round-trips.
    # MFA cookie: avoids the OTP challenge.
    # Seamless token: avoids the full 4-step credential login entirely.
    updated_data = dict(entry.data)
    needs_update = False

    acquired_mfa = hub.bridge.auth.mfa_cookie
    if acquired_mfa and acquired_mfa != entry.data.get(CONF_MFA_COOKIE, ""):
        updated_data[CONF_MFA_COOKIE] = acquired_mfa
        needs_update = True

    acquired_token = hub.bridge.auth.seamless_token
    if acquired_token and acquired_token != entry.data.get(CONF_SEAMLESS_TOKEN, ""):
        updated_data[CONF_SEAMLESS_TOKEN] = acquired_token
        needs_update = True

    if needs_update:
        hass.config_entries.async_update_entry(entry, data=updated_data)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {DATA_BRIDGE: hub}

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    hub: AlarmHub = hass.data[DOMAIN][entry.entry_id][DATA_BRIDGE]

    # Persist the latest seamless token before platforms are torn down so that
    # tokens rotated during runtime re-auths survive a reload or HA restart.
    token = hub.bridge.auth.seamless_token
    if token and token != entry.data.get(CONF_SEAMLESS_TOKEN, ""):
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_SEAMLESS_TOKEN: token}
        )

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        try:
            await hub.shutdown()
        finally:
            hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
