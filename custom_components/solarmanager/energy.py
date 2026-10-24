# energy.py — PV-Prognose für das HA-Energie-Dashboard
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .coordinator import SolarmanagerCoordinator


async def async_get_solar_forecast(
    hass: HomeAssistant, config_entry_id: str
) -> dict[str, dict[str, float | int]] | None:
    """Prognose (Wh je Stunde) für die gestrichelte Linie im Energie-Dashboard.

    None im lokalen Modus (keine Prognose) und solange noch keine geladen ist.
    """
    entry = hass.config_entries.async_get_entry(config_entry_id)
    coord = getattr(entry, "runtime_data", None) if entry else None
    if not isinstance(coord, SolarmanagerCoordinator) or coord.forecast is None:
        return None

    return {
        "wh_hours": {
            timestamp.isoformat(): round(wh)
            for timestamp, wh in coord.forecast.wh_hours(
                dt_util.get_default_time_zone()
            ).items()
        }
    }
