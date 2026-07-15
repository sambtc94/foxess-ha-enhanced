from __future__ import annotations

import json
import logging
import time

from homeassistant.components.rest.data import RestData
from homeassistant.components.select import SelectEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util.ssl import SSLCipherList

from . import DOMAIN
from .sensor import (
    DEFAULT_ENCODING,
    DEFAULT_TIMEOUT,
    DEFAULT_USE_V1_API,
    DEFAULT_VERIFY_SSL,
    METHOD_POST,
    GetAuth,
    _ENDPOINT_OA_DOMAIN,
    waitforAPI,
)

_LOGGER = logging.getLogger(__name__)

_ENDPOINT_OA_SETTING_SET = "/op/v0/device/setting/set"
_ENDPOINT_OA_SETTING_SET_V1 = "/op/v1/device/setting/set"

WORK_MODES = {
    "Self-Use": "SelfUse",
    "Backup": "Backup",
    "Feed-In": "Feedin",
    "Peak Shaving": "PeakShaving",
}


class FoxESSWorkModeSelect(CoordinatorEntity, SelectEntity):
    _attr_options = list(WORK_MODES.keys())
    _attr_icon = "mdi:battery-sync"

    def __init__(self, coordinator, name, deviceID, deviceSN, apiKey):
        super().__init__(coordinator=coordinator)
        self._attr_name = name + " - Work Mode"
        self._attr_unique_id = deviceID + "work-mode-select"
        self._deviceSN = deviceSN
        self._apiKey = apiKey
        self._deviceID = deviceID

    @property
    def current_option(self):
        if self.coordinator.data and "workMode" in self.coordinator.data:
            mode = self.coordinator.data["workMode"]
            for display, api_val in WORK_MODES.items():
                if api_val == mode:
                    return display
        return None

    @property
    def device_info(self):
        from homeassistant.helpers.entity import DeviceInfo

        info = DeviceInfo(
            identifiers={(DOMAIN, self._deviceID)},
            name=self.coordinator.name_prefix,
            manufacturer="FoxESS",
        )
        if self.coordinator.data and "addressbook" in self.coordinator.data:
            ab = self.coordinator.data["addressbook"]
            model = ab.get("deviceType")
            if model:
                info["model"] = model
            sw = ab.get("masterVersion")
            if sw and sw != "not provided":
                info["sw_version"] = sw
        return info

    async def async_select_option(self, option: str) -> None:
        api_value = WORK_MODES[option]
        await setWorkMode(
            self.hass,
            self._deviceSN,
            self._apiKey,
            api_value,
            coordinator=self.coordinator,
        )
        self.coordinator.data["workMode"] = api_value
        self.coordinator.async_set_updated_data(self.coordinator.data)


async def setWorkMode(hass, devicesn, apiKey, mode, coordinator=None):
    await waitforAPI(coordinator)

    v1_api = coordinator.v1_api if coordinator is not None else DEFAULT_USE_V1_API
    path = _ENDPOINT_OA_SETTING_SET_V1 if v1_api else _ENDPOINT_OA_SETTING_SET
    headerData = GetAuth().get_signature(token=apiKey, path=path)
    payload = json.dumps({"sn": devicesn, "key": "WorkMode", "value": mode})
    rest = RestData(
        hass,
        METHOD_POST,
        _ENDPOINT_OA_DOMAIN + path,
        DEFAULT_ENCODING,
        None,
        headerData,
        None,
        payload,
        DEFAULT_VERIFY_SSL,
        SSLCipherList.PYTHON_DEFAULT,
        DEFAULT_TIMEOUT,
    )

    timestamp = round(time.time() * 1000)
    await rest.async_update()
    if not rest.data:
        if v1_api:
            _LOGGER.debug("FoxESS work mode update returned no body on v1 API; assuming success")
            if coordinator is not None:
                response_time = round(time.time() * 1000) - timestamp
                coordinator.data.setdefault("raw", {})["ResponseTime"] = max(response_time, 0)
            return
        raise HomeAssistantError("FoxESS work mode update returned no data")

    response = json.loads(rest.data)
    if response.get("errno") != 0:
        _LOGGER.error("FoxESS work mode update failed: %s", response)
        raise HomeAssistantError("FoxESS work mode update failed")

    if coordinator is not None:
        response_time = round(time.time() * 1000) - timestamp
        coordinator.data.setdefault("raw", {})["ResponseTime"] = max(response_time, 0)


def _device_info(coordinator, deviceID):
    from homeassistant.helpers.entity import DeviceInfo

    info = DeviceInfo(
        identifiers={(DOMAIN, deviceID)},
        name=coordinator.name_prefix,
        manufacturer="FoxESS",
    )
    if coordinator.data and "addressbook" in coordinator.data:
        ab = coordinator.data["addressbook"]
        model = ab.get("deviceType")
        if model:
            info["model"] = model
        sw = ab.get("masterVersion")
        if sw and sw != "not provided":
            info["sw_version"] = sw
    return info


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    name = entry.data.get("name", coordinator.name_prefix)
    device_id = entry.data["deviceID"]
    device_sn = entry.data["deviceSN"]
    api_key = entry.data["apiKey"]

    entities = [
        FoxESSWorkModeSelect(coordinator, name, device_id, device_sn, api_key),
    ]
    async_add_entities(entities)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    return
