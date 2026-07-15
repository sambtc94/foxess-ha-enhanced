from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

DOMAIN = "foxess"
PLATFORMS = ["sensor", "select", "number"]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the FoxESS integration."""
    hass.data.setdefault(DOMAIN, {})
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
