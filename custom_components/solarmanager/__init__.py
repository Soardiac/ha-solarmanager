# __init__.py
from __future__ import annotations
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    issue_registry as ir,
)

from .const import CONF_API_KEY, CONF_SM_ID, DOMAIN, PLATFORMS
from .coordinator import SolarmanagerCoordinator, daily_store
from .entity import site_device_info

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# E-Mail/Passwort-Login (v1) wird von Solar Manager zu diesem Datum abgeschaltet.
PASSWORD_AUTH_DEADLINE = "30.06.2027"
MIGRATION_URL = (
    "https://github.com/Soardiac/ha-solarmanager#migration-für-bestehende-nutzer"
)


def _password_auth_issue_id(entry: ConfigEntry) -> str:
    return f"deprecated_password_auth_{entry.entry_id}"


def _review_password_auth_issue(
    hass: HomeAssistant, entry: ConfigEntry, coord: SolarmanagerCoordinator
) -> None:
    """Repair-Issue erzeugen/entfernen je nach Auth-Zustand.

    Erscheint nur bei echtem Handlungsbedarf: Cloud-Modus ohne API-Key läuft über
    den v1-Login und bricht am PASSWORD_AUTH_DEADLINE. Ist ein API-Key gesetzt
    (oder Local-Modus), wird ein evtl. vorhandenes Issue wieder gelöscht.
    """
    issue_id = _password_auth_issue_id(entry)
    needs_warning = not coord.is_local and not entry.data.get(CONF_API_KEY)
    if needs_warning:
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="deprecated_password_auth",
            translation_placeholders={
                "deadline": PASSWORD_AUTH_DEADLINE,
                "sm_id": entry.data.get(CONF_SM_ID, ""),
            },
            data={"entry_id": entry.entry_id},
            learn_more_url=MIGRATION_URL,
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, issue_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Solarmanager from a config entry."""
    coord = SolarmanagerCoordinator(hass, entry)

    # Migrationswarnung vor dem ersten Refresh setzen: Sie soll auch dann
    # erscheinen, wenn der v1-Login bereits scheitert und der Entry nicht lädt.
    _review_password_auth_issue(hass, entry, coord)

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

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload Solarmanager config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Persistierte Tageszähler löschen, wenn der Eintrag entfernt wird."""
    ir.async_delete_issue(hass, DOMAIN, _password_auth_issue_id(entry))
    await daily_store(hass, entry.entry_id).async_remove()


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow removing a device if it is no longer reported by the API."""
    coord: SolarmanagerCoordinator | None = getattr(config_entry, "runtime_data", None)
    for domain, identifier in device_entry.identifiers:
        if domain != DOMAIN:
            continue
        if identifier.startswith("site_"):
            # Das Site-Gerät gehört fest zum Config-Entry
            return False
        if identifier.startswith("device_"):
            if coord is None:
                # Entry nicht geladen (z. B. Setup-Fehler) — keine Metadaten,
                # Entfernen erlauben; Geräte werden beim nächsten Setup neu angelegt
                return True
            dev_id = identifier[len("device_"):]
            return dev_id not in coord.device_meta
    return True
