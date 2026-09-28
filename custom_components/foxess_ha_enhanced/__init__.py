from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import voluptuous as vol
import homeassistant.helpers.config_validation as cv

DOMAIN = "foxess_ha_enhanced"
PLATFORMS = ["sensor", "select", "number", "switch"]

SERVICE_SET_SCHEDULER = "set_scheduler"
SERVICE_REFRESH_SCHEDULER = "refresh_scheduler"
SCHEDULER_MODES = vol.In(
    ["SelfUse", "Feedin", "Backup", "ForceCharge", "ForceDischarge"]
)
SCHEDULER_NUMBER = vol.All(vol.Coerce(float))
SCHEDULER_GROUP_SCHEMA = vol.Schema(
    {
        vol.Required("startHour"): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
        vol.Required("startMinute"): vol.All(vol.Coerce(int), vol.Range(min=0, max=59)),
        vol.Required("endHour"): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
        vol.Required("endMinute"): vol.All(vol.Coerce(int), vol.Range(min=0, max=59)),
        vol.Required("workMode"): SCHEDULER_MODES,
        vol.Optional("enable"): vol.All(vol.Coerce(int), vol.In([0, 1])),
        vol.Optional("minSocOnGrid"): SCHEDULER_NUMBER,
        vol.Optional("fdSoc"): SCHEDULER_NUMBER,
        vol.Optional("fdPwr"): SCHEDULER_NUMBER,
        vol.Optional("maxSoc"): SCHEDULER_NUMBER,
        vol.Optional("importLimit"): SCHEDULER_NUMBER,
        vol.Optional("exportLimit"): SCHEDULER_NUMBER,
        vol.Optional("pvLimit"): SCHEDULER_NUMBER,
        vol.Optional("reactivePower"): SCHEDULER_NUMBER,
    },
    extra=vol.ALLOW_EXTRA,
)
SERVICE_SET_SCHEDULER_SCHEMA = vol.Schema(
    {
        vol.Required("device_sn"): cv.string,
        vol.Required("groups"): vol.All(
            cv.ensure_list, vol.Length(min=1), [SCHEDULER_GROUP_SCHEMA]
        ),
        vol.Optional("is_default", default=False): cv.boolean,
    }
)
SERVICE_REFRESH_SCHEDULER_SCHEMA = vol.Schema(
    {vol.Required("device_sn"): cv.string}
)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the FoxESS integration."""
    hass.data.setdefault(DOMAIN, {})

    async def handle_set_scheduler(call):
        from .switch import set_scheduler_groups

        device_sn = call.data["device_sn"]
        coordinator = next(
            (
                item
                for item in hass.data[DOMAIN].values()
                if getattr(item, "device_sn", None) == device_sn
            ),
            None,
        )
        if coordinator is None:
            raise HomeAssistantError(f"FoxESS device not found: {device_sn}")

        result = await set_scheduler_groups(
            hass,
            device_sn,
            coordinator.api_key,
            call.data["groups"],
            call.data["is_default"],
            coordinator,
        )
        coordinator.data["schedulerGroups"] = result
        coordinator.async_set_updated_data(coordinator.data)
        await coordinator.async_refresh()

    async def handle_refresh_scheduler(call):
        from .sensor import getSchedulerGroups

        device_sn = call.data["device_sn"]
        coordinator = next(
            (
                item
                for item in hass.data[DOMAIN].values()
                if getattr(item, "device_sn", None) == device_sn
            ),
            None,
        )
        if coordinator is None:
            raise HomeAssistantError(f"FoxESS device not found: {device_sn}")

        if await getSchedulerGroups(
            hass,
            coordinator.data,
            device_sn,
            coordinator.api_key,
            coordinator,
        ):
            raise HomeAssistantError("FoxESS scheduler refresh failed")

        coordinator.async_set_updated_data(coordinator.data)

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_SCHEDULER,
        handle_set_scheduler,
        schema=SERVICE_SET_SCHEDULER_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REFRESH_SCHEDULER,
        handle_refresh_scheduler,
        schema=SERVICE_REFRESH_SCHEDULER_SCHEMA,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up FoxESS from a config entry."""
    from .sensor import create_foxess_coordinator

    hass.data.setdefault(DOMAIN, {})
    coordinator = await create_foxess_coordinator(hass, entry.data)
    if not coordinator.last_update_success:
        return False

    hass.data[DOMAIN][entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a FoxESS config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        if not hass.data.get(DOMAIN):
            hass.data.pop(DOMAIN, None)
    return unload_ok
