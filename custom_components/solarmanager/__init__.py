# __init__.py
from __future__ import annotations
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .const import DOMAIN, PLATFORMS
from .coordinator import SolarmanagerCoordinator, daily_store
from .entity import site_device_info

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Solarmanager from a config entry."""
    coord = SolarmanagerCoordinator(hass, entry)
    await coord.async_config_entry_first_refresh()

    entry.runtime_data = coord

    # Site-Gerät explizit registrieren bevor Plattformen geladen werden,
    # damit via_device-Referenzen in number/select/datetime/binary_sensor greifen.
    info = site_device_info(coord)
    registry = dr.async_get(hass)
    registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers=info["identifiers"],
        name=info["name"],
        manufacturer=info["manufacturer"],
        model=info["model"],
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Bei Options-/Version-Änderungen Integration sauber neu laden
    entry.async_on_unload(entry.add_update_listener(_reload_on_update))
    return True


async def _reload_on_update(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload Solarmanager config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Persistierte Tageszähler löschen, wenn der Eintrag entfernt wird."""
    await daily_store(hass, entry.entry_id).async_remove()


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow removing a device if it is no longer reported by the API."""
    coord: SolarmanagerCoordinator = config_entry.runtime_data
    for domain, identifier in device_entry.identifiers:
        if domain != DOMAIN:
            continue
        if identifier.startswith("site_"):
            # Das Site-Gerät gehört fest zum Config-Entry
            return False
        if identifier.startswith("device_"):
            dev_id = identifier[len("device_"):]
            return dev_id not in coord.device_meta
    return True
