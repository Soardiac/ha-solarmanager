# entity.py — gemeinsame Helper für alle Plattformen
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

from .const import DOMAIN, ENTRY_TITLE, MANUFACTURER, MODEL, MODEL_LOCAL
from .coordinator import SolarmanagerCoordinator

SITE_DEVICE_NAME = ENTRY_TITLE


def site_identifier(entry_id: str) -> str:
    """Stabiler Identifier des Site-Geräts.

    Hängt an der entry_id statt an Host bzw. smId: Ein Moduswechsel
    (Cloud <-> Lokal) oder eine neue Gateway-IP erzeugt so kein zweites
    Site-Gerät mehr. Bestehende Geräte stellt async_migrate_entry() um.
    """
    return f"site_{entry_id}"


def find_device(data: dict[str, Any] | None, dev_id: str) -> dict[str, Any] | None:
    """Gerät aus der devices[]-Liste des Streams anhand der _id suchen."""
    for it in (data or {}).get("devices", []) or []:
        if str(it.get("_id")) == dev_id:
            return it
    return None


def local_day_bounds(now: datetime, offset_days: int = 0) -> tuple[datetime, datetime]:
    """Lokaler Tag [Mitternacht, nächste Mitternacht) — korrekt auch bei Zeitumstellung."""
    day = dt_util.as_local(now).date() + timedelta(days=offset_days)
    return (
        dt_util.start_of_local_day(day),
        dt_util.start_of_local_day(day + timedelta(days=1)),
    )


def _site_model(coordinator: SolarmanagerCoordinator) -> str:
    if not coordinator.is_local:
        return MODEL
    if coordinator.is_cloud_extension:
        return f"{MODEL_LOCAL} + Cloud"
    return MODEL_LOCAL


def site_device_info(coordinator: SolarmanagerCoordinator) -> dict[str, Any]:
    """device_info für das Site-Gerät (Gateway)."""
    site_id = coordinator.site_id
    # Bewusst ohne smId/Host im Namen: Der Gerätename wird Präfix der Entity-IDs
    # (sensor.solar_manager_…). Die Kennung steht stattdessen als Seriennummer
    # auf der Geräteseite.
    return {
        "identifiers": {(DOMAIN, site_identifier(coordinator.entry.entry_id))},
        "name": SITE_DEVICE_NAME,
        "serial_number": site_id,
        "manufacturer": MANUFACTURER,
        "model": _site_model(coordinator),
    }


def child_device_info(
    coordinator: SolarmanagerCoordinator,
    dev_id: str,
) -> dict[str, Any]:
    """device_info für ein untergeordnetes Gerät (verknüpft mit der Site)."""
    friendly = coordinator.get_device_name(dev_id)
    short = dev_id[-6:] if len(dev_id) >= 6 else dev_id
    info: dict[str, Any] = {
        "identifiers": {(DOMAIN, f"device_{dev_id}")},
        "name": friendly or f"Solarmanager Gerät {short}",
        "manufacturer": MANUFACTURER,
        "model": "Stream device",
    }
    # Verknüpfung über die Registry-ID (`via_device_id`, ab HA 2026.8). Das alte
    # `via_device` (Identifier-Tupel) löst ab 2026.9 einen RuntimeError aus und
    # verhinderte die Kind-Geräte-Sensoren (Issue #30) -- es darf nie gesetzt
    # werden, auch nicht als Fallback. Ohne registrierte Site-Device-ID bleibt
    # die Verknüpfung weg; das Gerät hängt dann nicht unter der Site, die
    # Entität entsteht aber.
    if coordinator.site_device_id:
        info["via_device_id"] = coordinator.site_device_id
    return info
