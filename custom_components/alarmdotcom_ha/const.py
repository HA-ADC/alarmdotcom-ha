"""Constants for the alarmdotcom_ha integration."""

from __future__ import annotations

DOMAIN = "alarmdotcom_ha"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_MFA_COOKIE = "mfa_cookie"
CONF_SEAMLESS_TOKEN = "seamless_token"
CONF_TWO_FACTOR_CODE = "two_factor_code"
CONF_BASE_URL = "base_url"
DATA_BRIDGE = "bridge"
WATER_METER_DEVICE_TYPE = "water-meter"

# Bus event fired for every momentary device event (e.g. a Z-Wave switch
# Central Scene press) that pyadc publishes on EventBrokerTopic.DEVICE_EVENT.
# Device triggers listen to this; it can also be used directly as an `event`
# trigger.
EVENT_DEVICE = f"{DOMAIN}_event"

# Central Scene support, modelled on HA's Z-Wave JS integration: one event
# entity per scene ("Scene 001" = on/up paddle, "Scene 002" = off/down paddle)
# whose event types are the Z-Wave key attribute names.
SCENES: tuple[int, ...] = (1, 2)
SCENE_EVENT_TYPES: tuple[str, ...] = (
    "KeyPressed",
    "KeyReleased",
    "KeyHeldDown",
    "KeyPressed2x",
    "KeyPressed3x",
    "KeyPressed4x",
    "KeyPressed5x",
)


def scene_id(scene: int) -> str:
    """Return the zero-padded scene label used by Z-Wave JS, e.g. ``002``."""
    return f"{scene:03d}"
