from __future__ import annotations

import json
import logging

from homeassistant.components.rest.data import RestData
from homeassistant.components.switch import SwitchEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util.ssl import SSLCipherList

from . import DOMAIN
from .sensor import (
    DEFAULT_ENCODING,
    DEFAULT_TIMEOUT,
    DEFAULT_VERIFY_SSL,
    GetAuth,
    METHOD_POST,
    _ENDPOINT_OA_DOMAIN,
    waitforAPI,
)

_LOGGER = logging.getLogger(__name__)
_ENDPOINT_OA_SCHEDULER_FLAG_SET_V1 = "/op/v1/device/scheduler/set/flag"


async def set_scheduler_enabled(hass, devicesn, api_key, enabled, coordinator=None):
    await waitforAPI(coordinator)

    path = _ENDPOINT_OA_SCHEDULER_FLAG_SET_V1
    headers = GetAuth().get_signature(token=api_key, path=path)
    payload = json.dumps({"deviceSN": devicesn, "enable": int(enabled)})
    rest = RestData(
        hass,
        METHOD_POST,
        _ENDPOINT_OA_DOMAIN + path,
        DEFAULT_ENCODING,
        None,
        headers,
        None,
        payload,
        DEFAULT_VERIFY_SSL,
        SSLCipherList.PYTHON_DEFAULT,
        DEFAULT_TIMEOUT,
    )

    await rest.async_update()
    if not rest.data:
        raise HomeAssistantError("FoxESS scheduler update returned no data")

    response = json.loads(rest.data)
    if response.get("errno") != 0:
        _LOGGER.error("FoxESS scheduler update failed: %s", response)
        raise HomeAssistantError("FoxESS scheduler update failed")


class FoxESSSchedulerSwitch(CoordinatorEntity, SwitchEntity):
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator, name, device_id, device_sn, api_key):
        super().__init__(coordinator=coordinator)
        self._attr_name = name + " - Mode Scheduler"
        self._attr_unique_id = device_id + "mode-scheduler-switch"
        self._device_id = device_id
        self._device_sn = device_sn
        self._api_key = api_key

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.get("schedulerSupported") is True

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.get("schedulerEnabled")

    @property
    def device_info(self):
        from homeassistant.helpers.entity import DeviceInfo

        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=self.coordinator.name_prefix,
            manufacturer="FoxESS",
        )

    async def async_turn_on(self, **kwargs) -> None:
        await set_scheduler_enabled(
            self.hass, self._device_sn, self._api_key, True, self.coordinator
        )
        self.coordinator.data["schedulerEnabled"] = True
        self.coordinator.async_set_updated_data(self.coordinator.data)

    async def async_turn_off(self, **kwargs) -> None:
        await set_scheduler_enabled(
            self.hass, self._device_sn, self._api_key, False, self.coordinator
        )
        self.coordinator.data["schedulerEnabled"] = False
        self.coordinator.async_set_updated_data(self.coordinator.data)


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    name = entry.data.get("name", coordinator.name_prefix)
    async_add_entities(
        [
            FoxESSSchedulerSwitch(
                coordinator,
                name,
                entry.data["deviceID"],
                entry.data["deviceSN"],
                entry.data["apiKey"],
            )
        ]
    )


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    return