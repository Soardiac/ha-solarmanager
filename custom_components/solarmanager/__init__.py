# __init__.py
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    config_validation as cv,
)
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers import (
    entity_registry as er,
)
from homeassistant.helpers import (
    issue_registry as ir,
)

from .const import (
    CONF_API_KEY,
    CONF_HOST,
    CONF_SM_ID,
    DOMAIN,
    ENTRY_TITLE,
    PASSWORD_AUTH_DEADLINE,
    PLATFORMS,
)
from .coordinator import SolarmanagerCoordinator, daily_store, site_id_from_data
from .entity import site_device_info, site_identifier

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

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


def _is_site_device(device: dr.DeviceEntry) -> bool:
    return any(
        domain == DOMAIN and ident.startswith("site_")
        for domain, ident in device.identifiers
    )


def _migrate_site_device(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Site-Gerät auf den stabilen Identifier site_{entry_id} umstellen.

    Bisher hing der Identifier an Host (lokal) bzw. smId (Cloud). Jeder
    Moduswechsel und jede neue Gateway-IP legte deshalb ein weiteres
    Site-Gerät an. Der neue Identifier wird dem bestehenden Geräteeintrag
    zugewiesen statt ein neues Gerät anzulegen: device_id, Nutzername, Bereich
    sowie Geräte-Trigger/-Aktionen in Automationen bleiben erhalten.

    Ausgewählt wird das Site-Gerät des aktuellen Modus, sonst das mit den
    meisten Entitäten. Übrige Site-Geräte ohne Entitäten sind Karteileichen
    früherer Wechsel und werden entfernt.
    """
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    target = (DOMAIN, site_identifier(entry.entry_id))

    candidates = [
        device
        for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id)
        if _is_site_device(device)
    ]
    if not candidates or any(target in d.identifiers for d in candidates):
        return

    def entity_count(device: dr.DeviceEntry) -> int:
        return len(
            er.async_entries_for_device(
                ent_reg, device.id, include_disabled_entities=True
            )
        )

    current = (DOMAIN, f"site_{site_id_from_data(entry.data)}")
    keep = max(
        candidates,
        key=lambda d: (current in d.identifiers, entity_count(d), d.modified_at),
    )
    dev_reg.async_update_device(keep.id, new_identifiers={target})
    _LOGGER.debug(
        "Site-Gerät %s auf Identifier %s umgestellt", keep.id, target[1]
    )

    for device in candidates:
        if device.id == keep.id:
            continue
        if entity_count(device):
            _LOGGER.warning(
                "Veraltetes Site-Gerät %s hat noch Entitäten und bleibt bestehen; "
                "es kann unter Einstellungen → Geräte gelöscht werden",
                device.id,
            )
            continue
        dev_reg.async_remove_device(device.id)
        _LOGGER.debug("Veraltetes Site-Gerät %s entfernt", device.id)


def _generated_titles(entry: ConfigEntry) -> set[str]:
    """Titel, die Config-Flow und Reconfigure bis 1.2 automatisch vergeben haben.

    Exakt gegen die Entry-Daten verglichen statt per Muster: ein eigener Titel
    wie „Solarmanager Garage“ bleibt so unangetastet.
    """
    titles = set()
    if sm_id := entry.data.get(CONF_SM_ID):
        titles.add(f"Solarmanager {sm_id}")
    if host := entry.data.get(CONF_HOST):
        titles.add(f"Solarmanager Local ({host})")
    return titles


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Config-Entry migrieren.

    1.1 -> 1.2: Site-Gerät auf stabilen Identifier umstellen (Discussion #26).
    1.2 -> 1.3: Automatisch erzeugten Titel durch „Solar Manager“ ersetzen.
    """
    if entry.version > 1:
        # Downgrade von einer künftigen Hauptversion: nicht unterstützt
        return False

    if entry.minor_version < 2:
        _migrate_site_device(hass, entry)
        hass.config_entries.async_update_entry(entry, minor_version=2)

    if entry.minor_version < 3:
        if entry.title in _generated_titles(entry):
            hass.config_entries.async_update_entry(entry, title=ENTRY_TITLE)
        hass.config_entries.async_update_entry(entry, minor_version=3)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Solarmanager from a config entry."""
    coord = SolarmanagerCoordinator(hass, entry)

    # Migrationswarnung vor dem ersten Refresh setzen: Sie soll auch dann
    # erscheinen, wenn der v1-Login bereits scheitert und der Entry nicht lädt.
    _review_password_auth_issue(hass, entry, coord)

    await coord.async_config_entry_first_refresh()

    entry.runtime_data = coord

    # Site-Gerät explizit registrieren bevor Plattformen geladen werden, damit die
    # Kind-Geräte in allen Plattformen darauf verweisen können. Die Registry-ID
    # merken: child_device_info() verknüpft darüber via via_device_id (ab HA 2026.8).
    info = site_device_info(coord)
    registry = dr.async_get(hass)
    site_device = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers=info["identifiers"],
        name=info["name"],
        manufacturer=info["manufacturer"],
        model=info["model"],
        serial_number=info["serial_number"],
    )
    coord.site_device_id = site_device.id

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
            # Das Site-Gerät gehört fest zum Config-Entry. Andere Site-Geräte
            # sind Überbleibsel, die die Migration nicht entfernen konnte.
            return identifier != site_identifier(config_entry.entry_id)
        if identifier.startswith("device_"):
            if coord is None:
                # Entry nicht geladen (z. B. Setup-Fehler) — keine Metadaten,
                # Entfernen erlauben; Geräte werden beim nächsten Setup neu angelegt
                return True
            dev_id = identifier[len("device_"):]
            return dev_id not in coord.device_meta
    return True
