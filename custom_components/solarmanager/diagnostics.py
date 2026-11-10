from __future__ import annotations

import time
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .api_client import SolarmanagerCloud
from .coordinator import SolarmanagerCoordinator

TO_REDACT: set[str] = {
    "password",
    "api_key",
    "cloud_api_key",
    "local_api_key",
    "email",
    "sm_id",
    "accessToken",
    "refreshToken",
    "Authorization",
}


def _cloud_client_info(client: SolarmanagerCloud) -> dict[str, Any]:
    remaining: float | None = None
    if client._exp_ts:
        remaining = round(client._exp_ts - time.time(), 1)
    return {
        "sm_id": "**REDACTED**",
        "token_type": client._token_type,
        "access_token": "**REDACTED**" if client._access else None,
        "refresh_token": "**REDACTED**" if client._refresh else None,
        "token_remaining_seconds": remaining,
        "uses_api_key": bool(client.api_key),
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    coord: SolarmanagerCoordinator = entry.runtime_data

    client = coord.client
    if client is None:
        client_info: dict[str, Any] = {"status": "not_initialized"}
    elif isinstance(client, SolarmanagerCloud):
        client_info = _cloud_client_info(client)
    else:  # SolarmanagerLocal
        client_info = {
            "mode": "local",
            "uses_api_key": bool(client._api_key),
            "cloud_extension": coord.is_cloud_extension,
        }
        if coord.cloud is not None:
            client_info["cloud"] = _cloud_client_info(coord.cloud)

    device_metadata: dict[str, Any] = {
        dev_id: {
            "name": meta.get("name"),
            "type": meta.get("type"),
            "raw": async_redact_data(dict(meta.get("raw") or {}), TO_REDACT),
        }
        for dev_id, meta in coord.device_meta.items()
    }

    forecast = coord.forecast
    forecast_info: dict[str, Any] = {
        "points": len(forecast.points) if forecast else 0,
        "first_start": forecast.first_start.isoformat() if forecast else None,
        "last_end": forecast.last_end.isoformat() if forecast else None,
        "fetched_at": coord._forecast_fetched_at,
        # Rohdaten der ersten Einträge: Damit lässt sich das echte Antwortformat prüfen
        "raw_sample": coord._forecast_raw_sample,
    }

    return {
        "config_entry": {
            "entry_id": entry.entry_id,
            # Der Titel enthält die sm_id im Klartext ("Solarmanager <sm_id>") und
            # würde sie an async_redact_data vorbei in öffentlich geteilte
            # Diagnose-Dateien tragen.
            "title": "**REDACTED**",
            "version": entry.version,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "coordinator": {
            "last_update_success": coord.last_update_success,
            "last_exception": str(coord.last_exception) if coord.last_exception else None,
            "update_interval_seconds": (
                coord.update_interval.total_seconds()
                if coord.update_interval is not None
                else None
            ),
            "data": async_redact_data(dict(coord.data) if coord.data else {}, TO_REDACT),
            "stats_data": async_redact_data(dict(coord._stats_data), TO_REDACT),
        },
        "forecast": forecast_info,
        "forecast_correction": {
            "window": coord.forecast_correction,
            "day": coord.forecast_correction_day,
            # Die letzten Viertelstunden: [Beginn, gemessen Wh, Prognose Wh, Messzeit s]
            "recent_slots": [
                [start.isoformat(), round(s.actual_wh, 1), round(s.forecast_wh, 1), round(s.seconds)]
                for start, s in sorted(coord._corr_slots.items())[-8:]
            ],
        },
        "generator": {
            "generator_kwp": coord.generator_kwp,
            "source": coord.generator_kwp_source,
            "api_kwp": coord.api_kwp,
        },
        "device_metadata": device_metadata,
        "client": client_info,
    }
