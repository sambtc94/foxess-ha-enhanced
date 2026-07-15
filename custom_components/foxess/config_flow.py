from __future__ import annotations

import json
import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.rest.data import RestData
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.util.ssl import SSLCipherList

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)

CONF_APIKEY = "apiKey"
CONF_DEVICEID = "deviceID"
CONF_DEVICESN = "deviceSN"
CONF_EVO = "Evo"
CONF_EXTPV = "extendPV"
CONF_NAME = "name"
CONF_REFRESH_INTERVAL = "refreshInterval"
CONF_RESTRICT = "Restrict"
CONF_USE_V1_API = "Use_V1_Api"
CONF_XTZONE = "xtZone"
DEFAULT_NAME = "FoxESS"
DEFAULT_REFRESH_INTERVAL = 5
DEFAULT_USE_V1_API = True
DEFAULT_VERIFY_SSL = False
DEFAULT_TIMEOUT = 75
DEFAULT_ENCODING = "UTF-8"
METHOD_GET = "GET"
_ENDPOINT_OA_DOMAIN = "https://www.foxesscloud.com"
_ENDPOINT_OA_DEVICE_DETAIL = "/op/v0/device/detail"
_ENDPOINT_OA_DEVICE_DETAIL_V1 = "/op/v1/device/detail"


class CannotConnect(Exception):
    """Error to indicate we cannot connect."""


class InvalidAuth(Exception):
    """Error to indicate invalid authentication."""


class GetAuth:
    def get_signature(self, token: str, path: str, lang: str = "en") -> dict[str, str]:
        import hashlib
        import time

        timestamp = round(time.time() * 1000)
        signature = rf"{path}\r\n{token}\r\n{timestamp}"
        return {
            "token": token,
            "lang": lang,
            "timestamp": str(timestamp),
            "Content-Type": "application/json",
            "signature": hashlib.md5(signature.encode("UTF-8")).hexdigest(),
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Connection": "close",
        }


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect."""
    path = _ENDPOINT_OA_DEVICE_DETAIL_V1 if data.get(CONF_USE_V1_API, DEFAULT_USE_V1_API) else _ENDPOINT_OA_DEVICE_DETAIL
    headers = GetAuth().get_signature(token=data[CONF_APIKEY], path=path)
    rest = RestData(
        hass,
        METHOD_GET,
        f"{_ENDPOINT_OA_DOMAIN}{path}?sn={data[CONF_DEVICESN]}",
        DEFAULT_ENCODING,
        None,
        headers,
        None,
        None,
        DEFAULT_VERIFY_SSL,
        SSLCipherList.PYTHON_DEFAULT,
        DEFAULT_TIMEOUT,
    )

    try:
        await rest.async_update()
    except Exception as exc:
        raise CannotConnect from exc

    if not rest.data:
        raise CannotConnect

    try:
        response = json.loads(rest.data)
    except json.JSONDecodeError as exc:
        raise CannotConnect from exc

    if response.get("errno") != 0:
        _LOGGER.debug("FoxESS validation error response: %s", response)
        raise InvalidAuth

    result = response.get("result") or {}
    return {
        "title": data[CONF_NAME],
        "device_sn": data[CONF_DEVICESN],
        "device_id": data[CONF_DEVICEID],
        "station_name": result.get("stationName"),
    }


class FoxessConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for FoxESS."""

    VERSION = 1

    def _get_schema(self, user_input: dict[str, Any] | None = None) -> vol.Schema:
        user_input = user_input or {}
        device_sn = user_input.get(CONF_DEVICESN, "")
        return vol.Schema(
            {
                vol.Required(CONF_APIKEY, default=user_input.get(CONF_APIKEY, "")): str,
                vol.Required(CONF_DEVICESN, default=device_sn): str,
                vol.Required(
                    CONF_DEVICEID,
                    default=user_input.get(CONF_DEVICEID, device_sn),
                ): str,
                vol.Optional(CONF_NAME, default=user_input.get(CONF_NAME, DEFAULT_NAME)): str,
                vol.Optional(
                    CONF_REFRESH_INTERVAL,
                    default=user_input.get(CONF_REFRESH_INTERVAL, DEFAULT_REFRESH_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=30)),
                vol.Optional(CONF_EXTPV, default=user_input.get(CONF_EXTPV, False)): bool,
                vol.Optional(CONF_XTZONE, default=user_input.get(CONF_XTZONE, False)): bool,
                vol.Optional(CONF_RESTRICT, default=user_input.get(CONF_RESTRICT, False)): bool,
                vol.Optional(
                    CONF_USE_V1_API,
                    default=user_input.get(CONF_USE_V1_API, DEFAULT_USE_V1_API),
                ): bool,
                vol.Optional(CONF_EVO, default=user_input.get(CONF_EVO, False)): bool,
            }
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            user_input[CONF_NAME] = user_input.get(CONF_NAME) or DEFAULT_NAME
            user_input[CONF_DEVICEID] = user_input.get(CONF_DEVICEID) or user_input[CONF_DEVICESN]
            await self.async_set_unique_id(user_input[CONF_DEVICESN])
            self._abort_if_unique_id_configured()
            try:
                info = await validate_input(self.hass, user_input)
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception during FoxESS config flow validation")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=self._get_schema(user_input),
            errors=errors,
        )
