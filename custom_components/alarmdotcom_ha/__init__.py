"""The alarmdotcom_ha integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from pyadc.exceptions import AuthenticationFailed, ServiceUnavailable

from .const import CONF_MFA_COOKIE, CONF_PASSWORD, CONF_USERNAME, DATA_BRIDGE, DOMAIN
from .hub import AlarmHub

log = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.ALARM_CONTROL_PANEL,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.COVER,
    Platform.IMAGE,
    Platform.LIGHT,
    Platform.LOCK,
    Platform.SENSOR,
    Platform.VALVE,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up alarmdotcom_ha from a config entry."""
    hub = AlarmHub(
        hass,
        entry,
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        mfa_cookie=entry.data.get(CONF_MFA_COOKIE, ""),
    )

    try:
        await hub.initialize()
    except AuthenticationFailed as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except ServiceUnavailable as err:
        raise ConfigEntryNotReady(str(err)) from err
    except Exception as err:
        raise ConfigEntryNotReady(str(err)) from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {DATA_BRIDGE: hub}

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hub: AlarmHub = hass.data[DOMAIN][entry.entry_id][DATA_BRIDGE]
        await hub.shutdown()
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
