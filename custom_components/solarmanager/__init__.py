# __init__.py
from __future__ import annotations
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS
from .coordinator import SolarmanagerCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up via YAML (nicht genutzt)."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Solarmanager from a config entry."""
    coord = SolarmanagerCoordinator(hass, entry)
    await coord.async_config_entry_first_refresh()

    # Für Plattformen verfügbar machen (sensor.py / number.py lesen hier raus)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coord

    # Plattformen laden (nimmt PLATFORMS aus const.py, z. B. ["sensor", "number"])
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Bei Options-/Version-Änderungen Integration sauber neu laden
    entry.async_on_unload(entry.add_update_listener(_reload_on_update))
    return True


async def _reload_on_update(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload Solarmanager config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok
