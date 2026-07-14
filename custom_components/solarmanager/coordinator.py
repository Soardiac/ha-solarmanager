# coordinator.py
from __future__ import annotations

from datetime import timedelta
import logging
import time
from typing import Any, NoReturn

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_SM_ID,
    CONF_API_KEY,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN,
    CLOUD_BASE,
    CONF_HOST,
    CONF_SCHEME,
    CONF_MODE,
    MODE_LOCAL,
)
from .api_client import (
    SolarmanagerCloud,
    SolarmanagerLocal,
    SolarmanagerAuthError,
    SolarmanagerApiError,
    SolarmanagerRateLimit,
)

_LOGGER = logging.getLogger(__name__)

# Geräte-Metadaten-Cache: /v1/info/sensors ist ein separater API-Call pro
# Refresh — nicht bei jedem 10s-Poll neu laden (Rate-Limit-Schonung). Nach
# PUTs wird gezielt via async_refresh_device_meta() aktualisiert.
META_TTL = 60  # Sekunden

# Tages-Statistiken (Cloud) höchstens alle 5 Minuten laden
STATS_TTL = 300  # Sekunden

# Persistenz der Tageszähler (überleben Neustart/Reload)
STORAGE_VERSION = 1
STORAGE_SAVE_DELAY = 60  # Sekunden

# Alle laut Swagger (BatteryModeAndSettingsSchema) gültigen Felder des
# PUT /v2/control/battery/{sensorId}. Das Schema hat kein required-Feld, aber
# Defaults auf fast allen Feldern; fehlende Keys werden serverseitig mit dem
# Default überschrieben. Daher immer das vollständige Settings-Objekt senden
# (read-modify-write), damit kein nicht-geändertes Feld zurückgesetzt wird.
BATTERY_PUT_FIELDS = frozenset({
    "batteryMode",
    "batteryManualMode",
    "maxChargePower",
    "maxDischargePower",
    "upperSocLimit",
    "lowerSocLimit",
    "powerCharge",
    "powerDischarge",
    "dischargeSocLimit",
    "morningSocLimit",
    "chargingSocLimit",
    "peakShavingSocDischargeLimit",
    "peakShavingSocMaxLimit",
    "peakShavingMaxGridPower",
    "peakShavingRechargePower",
    "tariffPriceLimit",
    "tariffPriceMode",
    "tariffAbsoluteChargeLimit",
    "tariffAbsoluteDischargeLimit",
    "tariffPercentageChargeLimit",
    "tariffPercentageDischargeLimit",
    "tariffOffsetChargeLimit",
    "tariffOffsetDischargeLimit",
    "tariffDischargeSocLimit",
    "tariffPriceLimitSocMax",
    "tariffPriceLimitSocMin",
    "tariffPriceLimitForecast",
    "standardStandaloneAllowed",
    "standardLowerSocLimit",
    "standardUpperSocLimit",
})


def daily_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    """Store für die persistierten Tageszähler eines Config-Entries."""
    return Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}_daily")


class SolarmanagerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator, der den v3-Stream pollt, Daten normalisiert und Geräte-Metadaten cached (aus /v1/info/sensors/{smId})."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN)
            ),
        )
        self.entry = entry
        self.is_local: bool = entry.data.get(CONF_MODE) == MODE_LOCAL
        self.client: SolarmanagerCloud | SolarmanagerLocal | None = None

        # Cache für Geräte-Metadaten: exakt per _id
        self.device_meta: dict[str, dict] = {}
        self._meta_last: float = 0.0

        # Tages-Statistiken (production, consumption, …) — nur Cloud
        self._stats_data: dict[str, Any] = {}
        self._stats_last: float = 0.0
        self._stats_date: str = ""

        # Tages-Energie (Lokal): Riemann-Integration von pW/cW/iW/eW
        self._local_production_wh: float = 0.0
        self._local_consumption_wh: float = 0.0
        self._local_grid_import_wh: float = 0.0
        self._local_grid_export_wh: float = 0.0
        self._local_day: str = ""
        self._local_t: float = 0.0

        # Tages-Batterieenergie (beide Modi): Summierung der bcWh/bdWh-Intervallwerte
        self._bat_charge_wh: float = 0.0
        self._bat_discharge_wh: float = 0.0
        self._bat_day: str = ""
        self._bat_last_t: Any = None  # Stream-Timestamp des zuletzt summierten Punkts

        # Persistenz der Tageszähler (Neustart/Reload mitten am Tag)
        self._store = daily_store(hass, entry.entry_id)
        self._store_loaded = False

    @property
    def site_id(self) -> str:
        if self.is_local:
            return self.entry.data[CONF_HOST]
        return self.entry.data.get(CONF_SM_ID, "unknown")

    async def _async_setup(self) -> None:
        if self.client:
            return
        session = async_get_clientsession(self.hass)
        if self.is_local:
            self.client = SolarmanagerLocal(
                self.entry.data[CONF_HOST],
                session,
                scheme=self.entry.data.get(CONF_SCHEME, "http"),
                api_key=self.entry.data.get(CONF_API_KEY),
            )
        else:
            self.client = SolarmanagerCloud(
                session,
                base=CLOUD_BASE,
                email=self.entry.data[CONF_EMAIL],
                password=self.entry.data[CONF_PASSWORD],
                sm_id=self.entry.data[CONF_SM_ID],
                api_key=self.entry.data.get(CONF_API_KEY),
            )
            # _async_setup wird vom Coordinator-Framework außerhalb von
            # _async_update_data aufgerufen — dessen Exception-Mapping greift
            # hier nicht. Ohne Mapping landet ein Auth-Fehler beim Login im
            # generischen Framework-Handler und löst nie den Reauth-Flow aus.
            try:
                await self.client.login()
            except (SolarmanagerAuthError, SolarmanagerRateLimit, SolarmanagerApiError) as err:
                self._raise_mapped_client_error(err)
        await self._async_restore_daily()
        await self._load_device_meta()

    # -------------------- Persistenz Tageszähler --------------------

    async def _async_restore_daily(self) -> None:
        """Gespeicherte Tageszähler laden, wenn sie vom heutigen Tag stammen."""
        if self._store_loaded:
            return
        self._store_loaded = True
        stored = await self._store.async_load() or {}
        today = dt_util.now().strftime("%Y-%m-%d")
        if stored.get("local_day") == today:
            self._local_day = today
            self._local_production_wh = float(stored.get("local_production_wh", 0.0))
            self._local_consumption_wh = float(stored.get("local_consumption_wh", 0.0))
            self._local_grid_import_wh = float(stored.get("local_grid_import_wh", 0.0))
            self._local_grid_export_wh = float(stored.get("local_grid_export_wh", 0.0))
            # _local_t bleibt 0 → die Lücke seit dem Neustart wird nicht integriert
        if stored.get("bat_day") == today:
            self._bat_day = today
            self._bat_charge_wh = float(stored.get("bat_charge_wh", 0.0))
            self._bat_discharge_wh = float(stored.get("bat_discharge_wh", 0.0))
            self._bat_last_t = stored.get("bat_last_t")

    def _daily_state(self) -> dict[str, Any]:
        return {
            "local_day": self._local_day,
            "local_production_wh": self._local_production_wh,
            "local_consumption_wh": self._local_consumption_wh,
            "local_grid_import_wh": self._local_grid_import_wh,
            "local_grid_export_wh": self._local_grid_export_wh,
            "bat_day": self._bat_day,
            "bat_charge_wh": self._bat_charge_wh,
            "bat_discharge_wh": self._bat_discharge_wh,
            "bat_last_t": self._bat_last_t,
        }

    # -------------------- Geräte-Metadaten --------------------

    async def _load_device_meta(self) -> None:
        """Geräte-Metadaten aus /v1/info/sensors/{smId} (Cloud) oder /v2/devices (Lokal) laden."""
        if not self.client:
            return
        # Auch bei Fehlern erst nach META_TTL erneut versuchen — sonst feuert
        # bei API-Störung jeder Poll einen zusätzlichen fehlschlagenden Request.
        self._meta_last = time.time()
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
            _LOGGER.debug("Loaded %d device metadata entries (info/devices)", len(self.device_meta))
        except Exception as e:
            _LOGGER.debug("Could not fetch device metadata from info/devices: %s", e)

    def get_device_name(self, dev_id: str) -> str | None:
        """Freundlichen Namen für ein Gerät liefern (exaktes Mapping via _id)."""
        m = self.device_meta.get(str(dev_id))
        return (m or {}).get("name")

    async def async_put_battery_merged(self, dev_id: str, changes: dict[str, Any]) -> None:
        """Batterie-Settings als vollständiges Objekt schreiben (read-modify-write).

        Liest die aktuellen Settings aus den gecachten Metadaten, überlagert die
        geänderten Felder und sendet das komplette Objekt. So setzt das Backend
        keine nicht-gesendeten Felder auf ihre Defaults zurück (siehe
        BATTERY_PUT_FIELDS).
        """
        raw_data = (self.device_meta.get(str(dev_id)) or {}).get("raw", {}).get("data", {}) or {}
        payload = {k: raw_data[k] for k in BATTERY_PUT_FIELDS if k in raw_data}
        if not payload:
            # Ohne gecachte Settings würde das Backend alle nicht gesendeten
            # Felder auf Defaults zurücksetzen — dann lieber gar nicht schreiben.
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="battery_settings_not_loaded",
            )
        payload.update(changes)
        await self.client.put_battery_settings(dev_id, payload)
        await self.async_refresh_device_meta()

    async def async_refresh_device_meta(self) -> None:
        """Meta sofort neu laden (z. B. nach einem PUT), dann Update anstoßen."""
        await self._load_device_meta()
        await self.async_request_refresh()

    async def _load_gateway_stats(self) -> None:
        """Tages-Statistiken via GET /v1/statistics/gateways/{smId} laden (alle 5 min)."""
        if not self.client:
            return
        # Auch bei Fehlern erst nach STATS_TTL erneut versuchen (Rate-Limit-Schonung)
        self._stats_last = time.time()
        try:
            now = dt_util.now()
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            from_dt = dt_util.as_utc(today_start).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            to_dt = dt_util.as_utc(now).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            data = await self.client.get_gateway_statistics(from_dt, to_dt, "high")
            self._stats_data = data
            _LOGGER.debug("Gateway stats loaded: %s", data)
        except Exception as e:
            _LOGGER.debug("Could not fetch gateway statistics: %s", e)

    def _raise_mapped_client_error(self, err: Exception) -> NoReturn:
        """Client-Exception auf ConfigEntryAuthFailed/UpdateFailed mappen.

        Einzige Stelle für dieses Mapping — wird sowohl beim Setup-Login
        (_async_setup) als auch im Update-Pfad (_async_update_data) gebraucht.
        """
        if isinstance(err, SolarmanagerAuthError):
            raise ConfigEntryAuthFailed(str(err)) from err
        if self.last_update_success:
            _LOGGER.warning("Solarmanager not available: %s", err)
        raise UpdateFailed(str(err)) from err

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            await self._async_setup()
            assert self.client is not None

            if self.is_local:
                assert isinstance(self.client, SolarmanagerLocal)
                raw = await self.client.get_point()
            else:
                assert isinstance(self.client, SolarmanagerCloud)
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
                # Energie-Intervallwerte (Wh) aus dem Stream
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
                # Cloud: iW/eW direkt vom API
                im = data["iW"] or 0.0
                ex = data["eW"] or 0.0
                data["gridW"] = im - ex
            elif self.is_local:
                # Lokal: iW/eW nicht in API → Energiebilanz
                grid = round(
                    (data["cW"] or 0.0)
                    + (data["bcW"] or 0.0)
                    - (data["pW"] or 0.0)
                    - (data["bdW"] or 0.0),
                    1,
                )
                data["gridW"] = grid
                data["iW"] = round(max(0.0, grid), 1)
                data["eW"] = round(max(0.0, -grid), 1)
            else:
                data["gridW"] = None

            # Meta nur nach Ablauf des TTL auffrischen (schont das API-Limit)
            if time.time() - self._meta_last > META_TTL:
                await self._load_device_meta()

            today = dt_util.now().strftime("%Y-%m-%d")

            # Tages-Statistiken: Cloud via API, Lokal via Integration
            if not self.is_local:
                if self._stats_date != today:
                    # Tageswechsel: Vortageswerte nicht weiterzeigen, bis der
                    # erste Fetch des neuen Tages gelungen ist
                    self._stats_data = {}
                    self._stats_date = today
                    self._stats_last = 0.0
                if time.time() - self._stats_last > STATS_TTL:
                    await self._load_gateway_stats()
                data["stat_production"] = self._stats_data.get("production")
                data["stat_consumption"] = self._stats_data.get("consumption")
                data["stat_self_consumption"] = self._stats_data.get("selfConsumption")
                _consumption = self._stats_data.get("consumption") or 0
                if _consumption < 10:  # Tagesübergang: Prozentwerte noch nicht aussagekräftig
                    data["stat_self_consumption_rate"] = None
                    data["stat_autarchy_degree"] = None
                else:
                    data["stat_self_consumption_rate"] = self._stats_data.get("selfConsumptionRate")
                    data["stat_autarchy_degree"] = self._stats_data.get("autarchyDegree")
                _sc = self._stats_data.get("selfConsumption") or 0
                data["stat_grid_import"] = max(0.0, (self._stats_data.get("consumption") or 0) - _sc)
                data["stat_grid_export"] = max(0.0, (self._stats_data.get("production") or 0) - _sc)
            else:
                # Lokal: pW/cW/iW/eW (W) über die Zeit integrieren → Wh-Tageszähler
                now_t = time.time()
                interval_s = (
                    self.update_interval.total_seconds()
                    if self.update_interval
                    else DEFAULT_SCAN
                )
                # Nach Ausfall/Suspend keine riesige Lücke mit dem aktuellen
                # Leistungswert integrieren (würde einen Energie-Spike erzeugen)
                max_gap_s = max(3 * interval_s, 30.0)
                if today != self._local_day:
                    self._local_production_wh = 0.0
                    self._local_consumption_wh = 0.0
                    self._local_grid_import_wh = 0.0
                    self._local_grid_export_wh = 0.0
                    self._local_day = today
                elif self._local_t > 0:
                    dt_s = now_t - self._local_t
                    if 0 < dt_s <= max_gap_s:
                        self._local_production_wh += (data.get("pW") or 0.0) * dt_s / 3600
                        self._local_consumption_wh += (data.get("cW") or 0.0) * dt_s / 3600
                        self._local_grid_import_wh += (data.get("iW") or 0.0) * dt_s / 3600
                        self._local_grid_export_wh += (data.get("eW") or 0.0) * dt_s / 3600
                    else:
                        _LOGGER.debug(
                            "Skipping local energy integration over %.0f s gap", dt_s
                        )
                self._local_t = now_t
                data["stat_production"] = self._local_production_wh
                data["stat_consumption"] = self._local_consumption_wh
                data["stat_self_consumption"] = max(0.0, self._local_production_wh - self._local_grid_export_wh)
                data["stat_grid_import"] = self._local_grid_import_wh
                data["stat_grid_export"] = self._local_grid_export_wh

            # Tages-Batterieenergie (beide Modi): bcWh/bdWh-Intervalle aufsummieren.
            # Nur neue Stream-Punkte zählen — bei unverändertem Timestamp t würde
            # dasselbe Intervall sonst mehrfach summiert (Poll < Stream-Intervall).
            # Punkte ohne Timestamp werden übersprungen: ohne t ist kein Dedup
            # möglich und jeder Poll würde dasselbe Intervall erneut summieren.
            if today != self._bat_day:
                self._bat_charge_wh = 0.0
                self._bat_discharge_wh = 0.0
                self._bat_day = today
                self._bat_last_t = None
            point_t = data.get("t")
            if point_t is not None and point_t != self._bat_last_t:
                self._bat_charge_wh += (data.get("bcWh") or 0.0)
                self._bat_discharge_wh += (data.get("bdWh") or 0.0)
                self._bat_last_t = point_t
            data["stat_bat_charge"] = self._bat_charge_wh
            data["stat_bat_discharge"] = self._bat_discharge_wh

            # Tageszähler verzögert persistieren (überleben Neustart/Reload)
            self._store.async_delay_save(self._daily_state, STORAGE_SAVE_DELAY)

            return data

        except (SolarmanagerAuthError, SolarmanagerRateLimit, SolarmanagerApiError) as err:
            self._raise_mapped_client_error(err)
        except HomeAssistantError:
            raise
        except Exception as err:
            if self.last_update_success:
                _LOGGER.warning("Unexpected error updating Solarmanager: %s", err)
            _LOGGER.debug("Traceback:", exc_info=True)
            raise UpdateFailed(f"Unexpected: {err}") from err
