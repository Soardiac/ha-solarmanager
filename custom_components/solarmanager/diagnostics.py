from __future__ import annotations

import time
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .coordinator import SolarmanagerCoordinator

TO_REDACT: set[str] = {
    "password",
    "api_key",
    "email",
    "sm_id",
    "accessToken",
    "refreshToken",
    "Authorization",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    coord: SolarmanagerCoordinator = entry.runtime_data

    client = coord.client
    if client is None:
        client_info: dict[str, Any] = {"status": "not_initialized"}
    else:
        remaining: float | None = None
        if client._exp_ts:
            remaining = round(client._exp_ts - time.time(), 1)
        client_info = {
            "sm_id": "**REDACTED**",
            "token_type": client._token_type,
            "access_token": "**REDACTED**" if client._access else None,
            "refresh_token": "**REDACTED**" if client._refresh else None,
            "token_remaining_seconds": remaining,
            "uses_api_key": bool(client.api_key),
        }

    device_metadata: dict[str, Any] = {
        dev_id: {
            "name": meta.get("name"),
            "type": meta.get("type"),
            "raw": async_redact_data(dict(meta.get("raw") or {}), TO_REDACT),
        }
        for dev_id, meta in coord.device_meta.items()
    }

    return {
        "config_entry": {
            "entry_id": entry.entry_id,
            "title": entry.title,
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
        "device_metadata": device_metadata,
        "client": client_info,
    }
