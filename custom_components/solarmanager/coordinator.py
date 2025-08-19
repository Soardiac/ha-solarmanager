# coordinator.py
from __future__ import annotations

from datetime import timedelta
import logging
import time
from typing import Any, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_SM_ID,
    CONF_API_KEY,          # optional; kann None sein
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN,
    CLOUD_BASE,
)
from .api_client import (
    SolarmanagerCloud,
    SolarmanagerAuthError,
    SolarmanagerApiError,
    SolarmanagerRateLimit,
)

_LOGGER = logging.getLogger(__name__)


class SolarmanagerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator, der den v3-Stream pollt, Daten normalisiert und Geräte-Metadaten cached (aus /v1/info/devices/{smId})."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN)
            ),
        )
        self.hass = hass
        self.entry = entry
        self.client: Optional[SolarmanagerCloud] = None

        # Cache für Geräte-Metadaten: exakt per _id
        self.device_meta: dict[str, dict] = {}
        self._meta_last: float = 0.0

    async def _async_setup(self) -> None:
        if self.client:
            return
        session = async_get_clientsession(self.hass)
        self.client = SolarmanagerCloud(
            session,
            base=CLOUD_BASE,
            email=self.entry.data[CONF_EMAIL],
            password=self.entry.data[CONF_PASSWORD],
            sm_id=self.entry.data[CONF_SM_ID],
            api_key=self.entry.data.get(CONF_API_KEY),  # meist None; Bearer ist Standard
        )
        await self.client.login()
        await self._load_device_meta()  # Namen laden

    async def _load_device_meta(self) -> None:
        """Geräte-Metadaten (Namen/Typen) aus /v1/info/devices/{smId} in device_meta mappen."""
        if not self.client:
            return
        try:
            raw = await self.client.list_devices()
            items = raw.get("items", raw) if isinstance(raw, dict) else raw
            meta: dict[str, dict] = {}
            for d in items or []:
                dev_id = str(d.get("_id") or "")
                if not dev_id:
                    continue
                # Name-Prio: expliziter "name" (falls vorhanden) sonst tag.name, sonst Typen-Fallback
                tag = d.get("tag") or {}
                friendly = d.get("name") or tag.get("name") or None
                typ = d.get("type") or d.get("device_type") or None
                if not friendly:
                    dg = d.get("device_group") or ""
                    if typ and dg:
                        friendly = f"{typ} ({dg})"
                    elif typ:
                        friendly = typ
                meta[dev_id] = {"name": friendly, "type": typ, "raw": d}
            self.device_meta = meta
            self._meta_last = time.time()
            _LOGGER.debug("Loaded %d device metadata entries (info/devices)", len(self.device_meta))
        except Exception as e:
            _LOGGER.debug("Could not fetch device metadata from info/devices: %s", e)

    def get_device_name(self, dev_id: str) -> str | None:
        """Freundlichen Namen für ein Gerät liefern (exaktes Mapping via _id)."""
        m = self.device_meta.get(str(dev_id))
        return (m or {}).get("name")
        
    async def async_get_battery_eco_settings(self, dev_id: str) -> dict[str, int]:
        """Liefert aktuelle Eco-Limits aus den Metadaten (mit sinnvollen Defaults)."""
        # Optional: Metadaten kurz auffrischen, falls älter
        if time.time() - self._meta_last > 30:
            await self._load_device_meta()
        meta = (self.device_meta.get(str(dev_id)) or {}).get("raw") or {}
        data = meta.get("data") or {}
        def _ival(x, default): 
            try: 
                return int(x) 
            except Exception: 
                return default
        return {
            "dischargeSocLimit": _ival(data.get("dischargeSocLimit"), 10),
            "morningSocLimit":   _ival(data.get("morningSocLimit"),   80),
            "chargingSocLimit":  _ival(data.get("chargingSocLimit"),  90),
        }

    async def async_refresh_device_meta(self) -> None:
        """Meta sofort neu laden und Coordinator refreshen (nach PUT)."""
        await self._load_device_meta()
        await self.async_request_refresh()

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            await self._async_setup()
            assert self.client is not None

            raw = await self.client.stream_user_v3()

            def _num(x):
                try:
                    return float(x) if x is not None else None
                except Exception:
                    return None

            def _to_int(x) -> int:
                try:
                    return int(x)
                except Exception:
                    return 0

            data: dict[str, Any] = {
                "t": raw.get("t"),
                "iv": _to_int(raw.get("iv", 0)),
                # Leistungen (W)
                "pW": _num(raw.get("pW")),   # PV
                "cW": _num(raw.get("cW")),   # Verbrauch
                "bcW": _num(raw.get("bcW")), # Batterie Laden +
                "bdW": _num(raw.get("bdW")), # Batterie Entladen +
                "iW": _num(raw.get("iW")),   # Netz Import +
                "eW": _num(raw.get("eW")),   # Netz Export +
                # Energie-Zähler (kWh) – deine Werte sind bereits kWh
                "pWh": _num(raw.get("pWh")),
                "cWh": _num(raw.get("cWh")),
                "iWh": _num(raw.get("iWh")),
                "eWh": _num(raw.get("eWh")),
                "bcWh": _num(raw.get("bcWh")),
                "bdWh": _num(raw.get("bdWh")),
                # Batterie
                "soc": _num(raw.get("soc")),
                # Geräte-Liste (aus dem Stream, IDs = _id → matchen exakt info/devices._id)
                "devices": raw.get("devices") or [],
            }

            # Abgeleitet: Batterie-Leistung (Laden +, Entladen -)
            if data["bcW"] is not None or data["bdW"] is not None:
                bc = data["bcW"] or 0.0
                bd = data["bdW"] or 0.0
                data["batW"] = bc - bd
            else:
                data["batW"] = None

            # Abgeleitet: Netzleistung (Import +, Export -)
            if data["iW"] is not None or data["eW"] is not None:
                im = data["iW"] or 0.0
                ex = data["eW"] or 0.0
                data["gridW"] = im - ex
            else:
                data["gridW"] = None

            # Meta alle 30 Minuten auffrischen
            if time.time() - self._meta_last > 1800:
                await self._load_device_meta()

            return data

        except (SolarmanagerAuthError, SolarmanagerRateLimit, SolarmanagerApiError) as err:
            raise UpdateFailed(str(err)) from err
        except Exception as err:
            _LOGGER.exception("Unexpected error in update: %s", err)
            raise UpdateFailed(f"Unexpected: {err}") from err
            
    async def async_refresh_device_meta(self) -> None:
        """Meta sofort neu laden (z. B. nach einem PUT), dann Update anstoßen."""
        await self._load_device_meta()
        await self.async_request_refresh()

