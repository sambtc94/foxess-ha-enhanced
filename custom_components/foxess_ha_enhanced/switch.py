from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN
from .number import setMaxCurrent

_LOGGER = logging.getLogger(__name__)

_RESTORE_DEFAULT_CURRENT = 25  # Amps used when no previous value has been stored


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


class FoxESSDischargeDisableSwitch(CoordinatorEntity, SwitchEntity):
    """Switch that disables battery discharge by setting MaxDischargeCurrent to 0.

    Turning the switch ON disables discharge (sets current to 0).
    Turning it OFF restores the previous non-zero discharge current.
    """

    _attr_icon = "mdi:battery-off-outline"

    def __init__(self, coordinator, name, deviceID, deviceSN, apiKey):
        super().__init__(coordinator=coordinator)
        self._attr_name = name + " - Disable Battery Discharge"
        self._attr_unique_id = deviceID + "disable-discharge-switch"
        self._deviceSN = deviceSN
        self._apiKey = apiKey
        self._deviceID = deviceID

    @property
    def device_info(self):
        return _device_info(self.coordinator, self._deviceID)

    @property
    def is_on(self) -> bool:
        current = self.coordinator.data.get("raw", {}).get("maxDischargeCurrent")
        if current is None:
            return False
        return int(current) == 0

    async def async_turn_on(self, **kwargs) -> None:
        current = self.coordinator.data.get("raw", {}).get("maxDischargeCurrent")
        if current is not None and int(current) != 0:
            self.coordinator.data["_prev_maxDischargeCurrent"] = int(current)
        await setMaxCurrent(
            self.hass, self._deviceSN, self._apiKey,
            "MaxDischargeCurrent", 0, coordinator=self.coordinator,
        )
        self.coordinator.data.setdefault("raw", {})["maxDischargeCurrent"] = 0
        self.coordinator.async_set_updated_data(self.coordinator.data)

    async def async_turn_off(self, **kwargs) -> None:
        restore = self.coordinator.data.get("_prev_maxDischargeCurrent", _RESTORE_DEFAULT_CURRENT)
        if restore == 0:
            restore = _RESTORE_DEFAULT_CURRENT
        await setMaxCurrent(
            self.hass, self._deviceSN, self._apiKey,
            "MaxDischargeCurrent", restore, coordinator=self.coordinator,
        )
        self.coordinator.data.setdefault("raw", {})["maxDischargeCurrent"] = restore
        self.coordinator.async_set_updated_data(self.coordinator.data)


class FoxESSChargeDisableSwitch(CoordinatorEntity, SwitchEntity):
    """Switch that disables battery charging by setting MaxChargeCurrent to 0.

    Turning the switch ON disables charging (sets current to 0).
    Turning it OFF restores the previous non-zero charge current.
    """

    _attr_icon = "mdi:battery-charging-off"

    def __init__(self, coordinator, name, deviceID, deviceSN, apiKey):
        super().__init__(coordinator=coordinator)
        self._attr_name = name + " - Disable Battery Charge"
        self._attr_unique_id = deviceID + "disable-charge-switch"
        self._deviceSN = deviceSN
        self._apiKey = apiKey
        self._deviceID = deviceID

    @property
    def device_info(self):
        return _device_info(self.coordinator, self._deviceID)

    @property
    def is_on(self) -> bool:
        current = self.coordinator.data.get("raw", {}).get("maxChargeCurrent")
        if current is None:
            return False
        return int(current) == 0

    async def async_turn_on(self, **kwargs) -> None:
        current = self.coordinator.data.get("raw", {}).get("maxChargeCurrent")
        if current is not None and int(current) != 0:
            self.coordinator.data["_prev_maxChargeCurrent"] = int(current)
        await setMaxCurrent(
            self.hass, self._deviceSN, self._apiKey,
            "MaxChargeCurrent", 0, coordinator=self.coordinator,
        )
        self.coordinator.data.setdefault("raw", {})["maxChargeCurrent"] = 0
        self.coordinator.async_set_updated_data(self.coordinator.data)

    async def async_turn_off(self, **kwargs) -> None:
        restore = self.coordinator.data.get("_prev_maxChargeCurrent", _RESTORE_DEFAULT_CURRENT)
        if restore == 0:
            restore = _RESTORE_DEFAULT_CURRENT
        await setMaxCurrent(
            self.hass, self._deviceSN, self._apiKey,
            "MaxChargeCurrent", restore, coordinator=self.coordinator,
        )
        self.coordinator.data.setdefault("raw", {})["maxChargeCurrent"] = restore
        self.coordinator.async_set_updated_data(self.coordinator.data)


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    name = entry.data.get("name", coordinator.name_prefix)
    device_id = entry.data["deviceID"]
    device_sn = entry.data["deviceSN"]
    api_key = entry.data["apiKey"]

    entities = [
        FoxESSDischargeDisableSwitch(coordinator, name, device_id, device_sn, api_key),
        FoxESSChargeDisableSwitch(coordinator, name, device_id, device_sn, api_key),
    ]
    async_add_entities(entities)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    return
