# entity.py — gemeinsame Helper für alle Plattformen
from __future__ import annotations

from typing import Any

from .const import DOMAIN, MANUFACTURER, MODEL, MODEL_LOCAL
from .coordinator import SolarmanagerCoordinator


def find_device(data: dict[str, Any] | None, dev_id: str) -> dict[str, Any] | None:
    """Gerät aus der devices[]-Liste des Streams anhand der _id suchen."""
    for it in (data or {}).get("devices", []) or []:
        if str(it.get("_id")) == dev_id:
            return it
    return None


def site_device_info(coordinator: SolarmanagerCoordinator) -> dict[str, Any]:
    """device_info für das Site-Gerät (Gateway)."""
    site_id = coordinator.site_id
    return {
        "identifiers": {(DOMAIN, f"site_{site_id}")},
        "name": f"Solarmanager {site_id}",
        "manufacturer": MANUFACTURER,
        "model": MODEL_LOCAL if coordinator.is_local else MODEL,
    }


def child_device_info(
    coordinator: SolarmanagerCoordinator,
    dev_id: str,
) -> dict[str, Any]:
    """device_info für ein untergeordnetes Gerät (via_device → Site)."""
    friendly = coordinator.get_device_name(dev_id)
    short = dev_id[-6:] if len(dev_id) >= 6 else dev_id
    return {
        "identifiers": {(DOMAIN, f"device_{dev_id}")},
        "name": friendly or f"Solarmanager Gerät {short}",
        "manufacturer": MANUFACTURER,
        "model": "Stream device",
        "via_device": (DOMAIN, f"site_{coordinator.site_id}"),
    }
