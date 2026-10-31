# entity.py — gemeinsame Helper für alle Plattformen
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.util import dt as dt_util

from .const import DOMAIN, MANUFACTURER, MODEL, MODEL_LOCAL
from .coordinator import SolarmanagerCoordinator

SITE_DEVICE_NAME = "Solar Manager"

# HA hat `via_device` (Identifier-Tupel) zugunsten von `via_device_id` (Registry-ID)
# abgelöst: ab 2026.8 gibt es beide Felder, ab 2026.9 nur noch `via_device_id`.
# Zur Laufzeit erkennen statt fest zu binden — die Mindestversion dieser
# Integration ist 2025.8, dort existiert `via_device_id` noch nicht.
#
# Wichtig: In 2026.9 eskaliert die Deprecation-Meldung von `via_device` zu einem
# RuntimeError, sobald HA im Stack keinen Frame der Integration findet. Genau das
# passiert im Sensor-Pfad und verhinderte, dass Kind-Geräte-Sensoren angelegt
# wurden (siehe Issue #30). Deshalb darf `via_device` auf neuem HA nicht mehr
# gesetzt werden — auch nicht als Fallback.
#
# `__optional_keys__` statt `__annotations__`: gleiches Ergebnis, wertet unter
# Python 3.14 (PEP 649) aber keine Annotationen aus. DeviceInfo ist total=False.
_SUPPORTS_VIA_DEVICE_ID = "via_device_id" in getattr(DeviceInfo, "__optional_keys__", ())


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


def site_device_info(coordinator: SolarmanagerCoordinator) -> dict[str, Any]:
    """device_info für das Site-Gerät (Gateway)."""
    site_id = coordinator.site_id
    # Bewusst ohne smId/Host im Namen: Der Gerätename wird Präfix der Entity-IDs
    # (sensor.solar_manager_…). Die Kennung steht stattdessen als Seriennummer
    # auf der Geräteseite. Die identifiers bleiben unverändert.
    return {
        "identifiers": {(DOMAIN, f"site_{site_id}")},
        "name": SITE_DEVICE_NAME,
        "serial_number": site_id,
        "manufacturer": MANUFACTURER,
        "model": MODEL_LOCAL if coordinator.is_local else MODEL,
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
    if _SUPPORTS_VIA_DEVICE_ID:
        # Ohne registrierte Site-Device-ID bleibt die Verknüpfung weg: Das Gerät
        # hängt dann nicht unter der Site, die Entität entsteht aber. Ein
        # via_device-Fallback wäre hier fatal — er löst den RuntimeError aus.
        if coordinator.site_device_id:
            info["via_device_id"] = coordinator.site_device_id
    else:
        info["via_device"] = (DOMAIN, f"site_{coordinator.site_id}")
    return info
