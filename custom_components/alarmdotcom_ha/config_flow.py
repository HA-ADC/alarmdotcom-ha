"""Config flow for the alarmdotcom_ha integration."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from pyadc import AlarmBridge
from pyadc.const import OtpType
from pyadc.exceptions import (
    AuthenticationFailed,
    MustConfigureMfa,
    OtpRequired,
    ServiceUnavailable,
)

from .const import (
    CONF_BASE_URL,
    CONF_MFA_COOKIE,
    CONF_PASSWORD,
    CONF_TWO_FACTOR_CODE,
    CONF_USERNAME,
    DOMAIN,
)

log = logging.getLogger(__name__)

_PROD_URL = "https://www.alarm.com"


def _normalize_base_url(url: str) -> str:
    """Normalize a base URL to a consistent ``https://host`` form.

    Accepts any of these formats and returns the canonical form:
      - ``alarm.com``
      - ``alarm.com/``
      - ``https://www.alarm.com/``
    """
    url = url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    return url


def _validate_base_url(raw: str) -> str | None:
    """Return an error key if the raw URL input is invalid, or None if acceptable.

    Validates the raw user input (before normalization) so that bad schemes
    and values with spaces are caught before https:// is prepended.
    """
    raw = raw.strip()
    if not raw:
        return "invalid_url"
    # If the user typed an explicit scheme, it must be http or https
    if "://" in raw:
        scheme = raw.split("://", 1)[0].lower()
        if scheme not in ("http", "https"):
            return "invalid_url"
    # Hostname portion (strip any scheme + path) must not contain spaces
    host_part = raw.split("://", 1)[-1].split("/")[0]
    if " " in host_part or not host_part:
        return "invalid_url"
    return None

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_BASE_URL, default=_PROD_URL): str,
    }
)

STEP_TWO_FACTOR_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TWO_FACTOR_CODE): str,
    }
)

STEP_TRUST_SCHEMA = vol.Schema(
    {
        vol.Optional("trust_device", default=False): bool,
    }
)


async def _validate_credentials(
    hass: HomeAssistant,
    username: str,
    password: str,
    mfa_cookie: str = "",
    base_url: str = _PROD_URL,
) -> dict[str, Any]:
    """Try logging in; return updated data dict or raise."""
    session = aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=15, connect=5)
    )
    try:
        bridge = AlarmBridge(
            session, username, password, mfa_cookie=mfa_cookie, base_url=base_url
        )
        await bridge.auth.login()
        new_mfa_cookie = bridge.auth.mfa_cookie
        await bridge.stop()
        return {
            CONF_USERNAME: username,
            CONF_PASSWORD: password,
            CONF_MFA_COOKIE: new_mfa_cookie,
            CONF_BASE_URL: base_url,
        }
    finally:
        await session.close()


class AlarmDotCom2ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for alarmdotcom_ha."""

    VERSION = 1

    def __init__(self) -> None:
        self._username: str = ""
        self._password: str = ""
        self._mfa_cookie: str = ""
        self._base_url: str = _PROD_URL
        self._otp_types: int = 0
        self._otp_method: int = 0  # OtpType int value chosen by user
        # Held open across 2FA steps so we can send/verify without re-logging in
        self._otp_session: aiohttp.ClientSession | None = None
        self._otp_bridge: AlarmBridge | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step — username + password."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._username = user_input[CONF_USERNAME]
            self._password = user_input[CONF_PASSWORD]
            raw_url = user_input.get(CONF_BASE_URL, _PROD_URL)
            self._base_url = _normalize_base_url(raw_url)

            if url_error := _validate_base_url(raw_url):
                errors[CONF_BASE_URL] = url_error
            else:
                session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=15, connect=5)
                )
                bridge = AlarmBridge(
                    session,
                    self._username,
                    self._password,
                    mfa_cookie=self._mfa_cookie,
                    base_url=self._base_url,
                )
                try:
                    await bridge.auth.login()
                    new_mfa_cookie = bridge.auth.mfa_cookie
                    await bridge.stop()
                    await session.close()
                    await self.async_set_unique_id(self._username.lower())
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=self._username,
                        data={
                            CONF_USERNAME: self._username,
                            CONF_PASSWORD: self._password,
                            CONF_MFA_COOKIE: new_mfa_cookie,
                            CONF_BASE_URL: self._base_url,
                        },
                    )
                except OtpRequired as exc:
                    self._otp_types = exc.otp_types
                    # Keep bridge alive across 2FA steps — don't close session
                    self._otp_session = session
                    self._otp_bridge = bridge
                    return await self.async_step_two_factor_method()
                except MustConfigureMfa:
                    await session.close()
                    return self.async_abort(reason="must_configure_mfa")
                except AuthenticationFailed:
                    await session.close()
                    errors["base"] = "invalid_auth"
                except ServiceUnavailable:
                    await session.close()
                    errors["base"] = "cannot_connect"
                except Exception:
                    await session.close()
                    log.exception("Unexpected error during alarmdotcom_ha setup")
                    errors["base"] = "unknown"

        schema = STEP_USER_SCHEMA
        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_two_factor_method(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Let the user choose how to receive their OTP code."""
        errors: dict[str, str] = {}

        # Build choices from the enabled OTP types
        choices: dict[str, str] = {}
        if self._otp_types & OtpType.SMS:
            choices["sms"] = "Text message (SMS)"
        if self._otp_types & OtpType.EMAIL:
            choices["email"] = "Email"
        if self._otp_types & OtpType.APP:
            choices["app"] = "Authenticator app"

        if user_input is not None:
            method = user_input.get("method", "")
            # Map string → OtpType int value (app=1, sms=2, email=4)
            method_map = {"app": 1, "sms": 2, "email": 4}
            self._otp_method = method_map.get(method, 0)
            bridge = self._otp_bridge
            try:
                if method == "sms":
                    await bridge.auth.send_otp_sms()
                elif method == "email":
                    await bridge.auth.send_otp_email()
                # app method doesn't need a send — code is already in the app
            except Exception:
                log.exception("Failed to send OTP via %s", method)
                errors["base"] = "otp_send_failed"

            if not errors:
                return await self.async_step_two_factor()

        return self.async_show_form(
            step_id="two_factor_method",
            data_schema=vol.Schema(
                {vol.Required("method"): vol.In(choices)}
            ),
            errors=errors,
        )

    async def async_step_two_factor(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle OTP code entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            code = user_input[CONF_TWO_FACTOR_CODE].strip()
            if not code.isdigit() or len(code) != 6:
                errors["base"] = "invalid_code_format"
            else:
                bridge = self._otp_bridge
                try:
                    self._mfa_cookie = await bridge.auth.verify_otp(code, otp_type=self._otp_method)
                except AuthenticationFailed:
                    errors["base"] = "invalid_code"
                except Exception:
                    log.exception("Unexpected error during OTP verification")
                    errors["base"] = "unknown"

            if not errors:
                return await self.async_step_trust_device()

        return self.async_show_form(
            step_id="two_factor",
            data_schema=STEP_TWO_FACTOR_SCHEMA,
            errors=errors,
        )

    async def async_step_trust_device(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Optionally trust this device to skip OTP next time."""
        if user_input is not None:
            bridge = self._otp_bridge
            if user_input.get("trust_device") and bridge:
                try:
                    await bridge.auth.trust_device()
                    self._mfa_cookie = bridge.auth.mfa_cookie
                    log.debug("trust_device step: mfa_cookie len=%d", len(self._mfa_cookie))
                except Exception as err:
                    log.warning("Could not mark device as trusted: %s", err)
                    log.info("Device trust failed; you will be prompted for a two-factor code on the next login.")

            # Done with the OTP bridge — clean up
            if bridge:
                try:
                    await bridge.stop()
                except Exception:
                    pass
            if self._otp_session:
                try:
                    await self._otp_session.close()
                except Exception:
                    pass
            self._otp_bridge = None
            self._otp_session = None

            await self.async_set_unique_id(self._username.lower())
            self._abort_if_unique_id_configured()
            log.info("Creating config entry: mfa_cookie present=%s", bool(self._mfa_cookie))
            return self.async_create_entry(
                title=self._username,
                data={
                    CONF_USERNAME: self._username,
                    CONF_PASSWORD: self._password,
                    CONF_MFA_COOKIE: self._mfa_cookie,
                    CONF_BASE_URL: self._base_url,
                },
            )

        return self.async_show_form(
            step_id="trust_device",
            data_schema=STEP_TRUST_SCHEMA,
        )

    async def async_step_reauth(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle re-authentication (e.g. after 1008 WS close)."""
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry:
            self._username = entry.data.get(CONF_USERNAME, "")
            self._password = entry.data.get(CONF_PASSWORD, "")
            self._mfa_cookie = entry.data.get(CONF_MFA_COOKIE, "")
            self._base_url = entry.data.get(CONF_BASE_URL, _PROD_URL)

        errors: dict[str, str] = {}

        if user_input is not None:
            self._password = user_input[CONF_PASSWORD]
            session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15, connect=5)
            )
            bridge = AlarmBridge(
                session,
                self._username,
                self._password,
                mfa_cookie=self._mfa_cookie,
                base_url=self._base_url,
            )
            try:
                await bridge.auth.login()
                new_mfa_cookie = bridge.auth.mfa_cookie
                await bridge.stop()
                await session.close()
                new_data = {
                    CONF_USERNAME: self._username,
                    CONF_PASSWORD: self._password,
                    CONF_MFA_COOKIE: new_mfa_cookie,
                    CONF_BASE_URL: self._base_url,
                }
                if entry:
                    self.hass.config_entries.async_update_entry(entry, data=new_data)
                    await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")
            except OtpRequired as exc:
                self._otp_types = exc.otp_types
                self._otp_session = session
                self._otp_bridge = bridge
                return await self.async_step_two_factor_method()
            except AuthenticationFailed:
                await session.close()
                errors["base"] = "invalid_auth"
            except ServiceUnavailable:
                await session.close()
                errors["base"] = "cannot_connect"
            except Exception:
                await session.close()
                log.exception("Unexpected error during alarmdotcom_ha re-auth")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="reauth",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
        )

