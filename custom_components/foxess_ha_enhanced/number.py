from __future__ import annotations

import json
import logging

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util.ssl import SSLCipherList

from . import DOMAIN
from .sensor import (
    DEFAULT_ENCODING,
    DEFAULT_TIMEOUT,
    DEFAULT_VERIFY_SSL,
    METHOD_POST,
    GetAuth,
    _ENDPOINT_OA_DOMAIN,
    waitforAPI,
)

_LOGGER = logging.getLogger(__name__)

_ENDPOINT_OA_BATTERY_SOC_SET = "/op/v0/device/battery/soc/set"


async def setBatterySoC(hass, devicesn, apiKey, minSoc, minSocOnGrid, coordinator=None):
    """Write both min-SoC thresholds to the FoxESS Cloud in one API call."""
    await waitforAPI(coordinator)

    path = _ENDPOINT_OA_BATTERY_SOC_SET
    headerData = GetAuth().get_signature(token=apiKey, path=path)
    payload = json.dumps({"sn": devicesn, "minSoc": minSoc, "minSocOnGrid": minSocOnGrid})

    from homeassistant.components.rest.data import RestData

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

    await rest.async_update()
    if not rest.data:
        raise HomeAssistantError("FoxESS battery SoC update returned no data")

    response = json.loads(rest.data)
    if response.get("errno") != 0:
        _LOGGER.error("FoxESS battery SoC update failed: %s", response)
        raise HomeAssistantError("FoxESS battery SoC update failed")


class FoxESSBatMinSoCNumber(CoordinatorEntity, NumberEntity):
    """Writable minimum battery State-of-Charge (off-grid limit)."""

    _attr_device_class = NumberDeviceClass.BATTERY
    _attr_native_min_value = 10
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:battery-low"

    def __init__(self, coordinator, name, deviceID, deviceSN, apiKey):
        super().__init__(coordinator=coordinator)
        self._attr_name = name + " - Min SoC"
        self._attr_unique_id = deviceID + "min-soc-number"
        self._deviceSN = deviceSN
        self._apiKey = apiKey
        self._deviceID = deviceID

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

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data and "battery" in self.coordinator.data:
            return self.coordinator.data["battery"].get("minSoc")
        return None

    async def async_set_native_value(self, value: float) -> None:
        min_soc_on_grid = (
            self.coordinator.data.get("battery", {}).get("minSocOnGrid") or 10
        )
        await setBatterySoC(
            self.hass,
            self._deviceSN,
            self._apiKey,
            int(value),
            min_soc_on_grid,
            coordinator=self.coordinator,
        )
        self.coordinator.data["battery"]["minSoc"] = int(value)
        self.coordinator.async_set_updated_data(self.coordinator.data)


class FoxESSBatMinSoCOnGridNumber(CoordinatorEntity, NumberEntity):
    """Writable minimum battery State-of-Charge when connected to the grid."""

    _attr_device_class = NumberDeviceClass.BATTERY
    _attr_native_min_value = 10
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:battery-low"

    def __init__(self, coordinator, name, deviceID, deviceSN, apiKey):
        super().__init__(coordinator=coordinator)
        self._attr_name = name + " - Min SoC on Grid"
        self._attr_unique_id = deviceID + "min-soc-on-grid-number"
        self._deviceSN = deviceSN
        self._apiKey = apiKey
        self._deviceID = deviceID

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

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data and "battery" in self.coordinator.data:
            return self.coordinator.data["battery"].get("minSocOnGrid")
        return None

    async def async_set_native_value(self, value: float) -> None:
        min_soc = (
            self.coordinator.data.get("battery", {}).get("minSoc") or 10
        )
        await setBatterySoC(
            self.hass,
            self._deviceSN,
            self._apiKey,
            min_soc,
            int(value),
            coordinator=self.coordinator,
        )
        self.coordinator.data["battery"]["minSocOnGrid"] = int(value)
        self.coordinator.async_set_updated_data(self.coordinator.data)


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            FoxESSBatMinSoCNumber(
                coordinator,
                entry.data.get("name", coordinator.name_prefix),
                entry.data["deviceID"],
                entry.data["deviceSN"],
                entry.data["apiKey"],
            ),
            FoxESSBatMinSoCOnGridNumber(
                coordinator,
                entry.data.get("name", coordinator.name_prefix),
                entry.data["deviceID"],
                entry.data["deviceSN"],
                entry.data["apiKey"],
            ),
        ]
    )


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    return
