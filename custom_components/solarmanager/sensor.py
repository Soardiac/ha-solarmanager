# sensor.py
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfEnergy,
    UnitOfLength,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .coordinator import DEVICE_DAILY_KEYS, SolarmanagerCoordinator, compute_surplus_w
from .entity import child_device_info, find_device, local_day_bounds, site_device_info
from .forecast import ForecastPoint, PvForecast

PARALLEL_UPDATES = 1

# --- Site-weite Sensoren (aus dem Stream) ---
# (key, translation_key)

POWER_SENSORS = [
    ("pW", "pv_power"),
    ("cW", "consumption_power"),
    ("batW", "battery_power"),
    ("iW", "grid_import_power"),
    ("eW", "grid_export_power"),
    ("gridW", "grid_power"),
]

# Intervallwerte (Wh) aus dem Stream — per Default deaktiviert
ENERGY_SENSORS = [
    ("pWh", "pv_energy_interval"),
    ("cWh", "consumption_energy_interval"),
    ("iWh", "grid_import_energy_interval"),
    ("eWh", "grid_export_energy_interval"),
    ("bcWh", "battery_charge_energy_interval"),
    ("bdWh", "battery_discharge_energy_interval"),
]

# Tages-Statistiken aus /v1/statistics/gateways/{smId} bzw. lokaler Integration
# (key, translation_key, unit, device_class, state_class)
STATS_SENSORS = [
    ("stat_production", "production_today", UnitOfEnergy.WATT_HOUR, SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING),
    ("stat_consumption", "consumption_today", UnitOfEnergy.WATT_HOUR, SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING),
    ("stat_self_consumption", "self_consumption_today", UnitOfEnergy.WATT_HOUR, SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING),
]

# Prozentwerte: Cloud liefert sie direkt vom API, lokal werden sie aus
# stat_production/stat_consumption/stat_self_consumption berechnet.
STATS_SENSORS_PERCENT = [
    ("stat_self_consumption_rate", "self_consumption_rate", PERCENTAGE, None, SensorStateClass.MEASUREMENT),
    ("stat_autarchy_degree", "autarchy_degree", PERCENTAGE, None, SensorStateClass.MEASUREMENT),
]

GRID_STATS_SENSORS = [
    ("stat_grid_import", "grid_import_today", UnitOfEnergy.WATT_HOUR, SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING),
    ("stat_grid_export", "grid_export_today", UnitOfEnergy.WATT_HOUR, SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING),
]

BAT_STATS_SENSORS = [
    ("stat_bat_charge", "battery_charge_today", UnitOfEnergy.WATT_HOUR, SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING),
    ("stat_bat_discharge", "battery_discharge_today", UnitOfEnergy.WATT_HOUR, SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING),
]

# PV-Prognose (nur Cloud) — (key, translation_key, Zeitfenster)
FORECAST_ENERGY_SENSORS = [
    ("forecast_energy_today", "forecast_energy_today", "today"),
    ("forecast_energy_remaining_today", "forecast_energy_remaining_today", "remaining_today"),
    ("forecast_energy_tomorrow", "forecast_energy_tomorrow", "tomorrow"),
]

# Länge der Prognose-Zeitreihe im Attribut von forecast_power_now
FORECAST_SERIES_HOURS = 48


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    coord: SolarmanagerCoordinator = entry.runtime_data

    # Site-Sensoren
    site_entities: list[SensorEntity] = [
        *(SolarmanagerPowerSensor(coord, key, tkey) for key, tkey in POWER_SENSORS),
        *(SolarmanagerEnergySensor(coord, key, tkey) for key, tkey in ENERGY_SENSORS),
        SocSensor(coord),
        PvSurplusPowerSensor(coord),
        DevicesOverviewSensor(coord),
        GeneratorKwpSensor(coord),
    ]
    site_entities += [SolarmanagerStatsSensor(coord, *spec) for spec in STATS_SENSORS]
    site_entities += [SolarmanagerStatsSensor(coord, *spec) for spec in STATS_SENSORS_PERCENT]
    site_entities += [SolarmanagerStatsSensor(coord, *spec) for spec in GRID_STATS_SENSORS]
    site_entities += [SolarmanagerStatsSensor(coord, *spec) for spec in BAT_STATS_SENSORS]
    if not coord.is_local:
        # PV-Prognose gibt es nur in der Cloud
        site_entities += [
            ForecastPowerSensor(coord, "forecast_power_now", "forecast_power_now", 0, with_series=True),
            ForecastPowerSensor(coord, "forecast_power_15m", "forecast_power_15m", 15),
            ForecastPowerSensor(coord, "forecast_power_30m", "forecast_power_30m", 30),
            *(ForecastEnergySensor(coord, key, tkey, window) for key, tkey, window in FORECAST_ENERGY_SENSORS),
            ForecastPeakPowerSensor(coord),
            ForecastPeakTimeSensor(coord),
        ]
    async_add_entities(site_entities, True)

    # Geräte-Sensoren dynamisch aus devices[] — auch für Geräte, die erst
    # nach dem Setup im Stream auftauchen (Coordinator-Listener).
    simple_sensors: list[tuple[str, type[_DeviceBase]]] = [
        ("power", DevicePowerSensor),
        ("soc", DeviceSocSensor),
        ("temperature", DeviceTemperatureSensor),
        ("activeDevice", DeviceActiveStateSensor),
        ("operationState", DeviceOperationStateSensor),
        ("switchState", DeviceSwitchStateSensor),
        ("heatingAdjustment", DeviceHeatingAdjustmentSensor),
        ("remainingRange", DeviceRemainingRangeSensor),
    ]
    daily_sensors = [
        ("iWhTotal", "daily_consumption"),
        ("eWhTotal", "daily_feed_in"),
    ]
    created: set[str] = set()

    @callback
    def _sync_device_sensors() -> None:
        new_entities: list[SensorEntity] = []
        for dev in (coord.data or {}).get("devices", []) or []:
            dev_id = str(dev.get("_id") or "")
            if not dev_id:
                continue
            for key, cls in simple_sensors:
                uid = f"{dev_id}_{key}"
                if key in dev and uid not in created:
                    created.add(uid)
                    new_entities.append(cls(coord, dev_id))
            for key, tkey in daily_sensors:
                uid = f"{dev_id}_{key}"
                if key in dev and uid not in created:
                    created.add(uid)
                    new_entities.append(DeviceDailyEnergySensor(coord, dev_id, key, tkey))
        if new_entities:
            async_add_entities(new_entities, True)

    _sync_device_sensors()
    entry.async_on_unload(coord.async_add_listener(_sync_device_sensors))


# ---------- Basisklassen ----------

class _Base(CoordinatorEntity[SolarmanagerCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: SolarmanagerCoordinator, key: str, translation_key: str):
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{key}"
        self._attr_translation_key = translation_key
        self._attr_device_info = site_device_info(coordinator)


class SolarmanagerPowerSensor(_Base, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    @property
    def native_value(self) -> float | None:
        v = (self.coordinator.data or {}).get(self._key)
        return float(v) if isinstance(v, (int, float)) else None


class SolarmanagerEnergySensor(_Base, SensorEntity):
    """Intervall-Energiewerte (Wh) aus dem Stream — keine Zähler, daher per
    Default aus und ohne ENERGY-device_class (erlaubt kein MEASUREMENT)."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    _attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> float | None:
        v = (self.coordinator.data or {}).get(self._key)
        try:
            return float(v) if v is not None else None
        except Exception:
            return None


class SolarmanagerStatsSensor(_Base, SensorEntity):
    """Tages-Statistik-Sensor (production, consumption, … aus /v1/statistics/gateways)."""

    def __init__(
        self,
        coordinator: SolarmanagerCoordinator,
        key: str,
        translation_key: str,
        unit: str,
        device_class,
        state_class,
    ) -> None:
        super().__init__(coordinator, key, translation_key)
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = state_class
        # Wh-Zähler ohne Nachkommastellen, Prozentwerte mit einer
        self._attr_suggested_display_precision = 1 if unit == PERCENTAGE else 0

    @property
    def native_value(self) -> float | None:
        v = (self.coordinator.data or {}).get(self._key)
        try:
            return float(v) if v is not None else None
        except Exception:
            return None


class SocSensor(_Base, SensorEntity):
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator: SolarmanagerCoordinator):
        super().__init__(coordinator, "soc", "battery_soc")

    @property
    def native_value(self) -> float | None:
        v = (self.coordinator.data or {}).get("soc")
        try:
            return float(v) if v is not None else None
        except Exception:
            return None


class PvSurplusPowerSensor(_Base, SensorEntity):
    """PV-Überschuss (W) = PV-Leistung - Hausverbrauch - Batterie-Leistung.

    Positiv = Überschuss (Richtung Netzeinspeisung/steuerbare Verbraucher),
    negativ = Defizit (Netzbezug/Batterie deckt die Lücke).
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, coordinator: SolarmanagerCoordinator):
        super().__init__(coordinator, "surplusW", "surplus_power")

    @property
    def native_value(self) -> float | None:
        return compute_surplus_w(self.coordinator.data)


class DevicesOverviewSensor(_Base, SensorEntity):
    """Zeigt Anzahl Geräte und komprimierte Attribute aus devices[].

    Diagnose-Sensor: schreibt bei jedem Poll neue Attribute in den Recorder,
    daher per Default deaktiviert.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: SolarmanagerCoordinator):
        super().__init__(coordinator, "devices_overview", "devices_overview")

    @property
    def native_value(self):
        devs = (self.coordinator.data or {}).get("devices") or []
        return len(devs)

    @property
    def extra_state_attributes(self):
        d = self.coordinator.data or {}
        devs = d.get("devices") or []
        compact = []
        for it in devs:
            compact.append({
                "id": it.get("_id"),
                "signal": it.get("signal"),
                "activeDevice": it.get("activeDevice"),
                "power_W": it.get("power"),
                "soc_%": it.get("soc"),
                "iWh_Wh": it.get("iWh"),
                "eWh_Wh": it.get("eWh"),
                "temperature_C": it.get("temperature"),
                "deviceState": it.get("deviceState"),
                "switchState": it.get("switchState"),
            })
        return {
            "timestamp": d.get("t"),
            "interval_s": d.get("iv"),
            "devices": compact,
        }


class GeneratorKwpSensor(_Base, SensorEntity):
    """Generatorleistung (kWp), mit der die Abregelungs-Conditions rechnen.

    Sichtbar gemacht, weil der Wert aus Solar Manager oft falsch erfasst ist
    (z. B. AC-Leistung des Wechselrichters statt Modulleistung). Attribut
    ``source`` zeigt, ob er aus den Optionen oder aus Solar Manager stammt.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "kWp"
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: SolarmanagerCoordinator):
        super().__init__(coordinator, "generator_kwp", "generator_kwp")

    @property
    def native_value(self) -> float | None:
        return self.coordinator.generator_kwp

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"source": self.coordinator.generator_kwp_source}


# ---------- PV-Prognose (nur Cloud) ----------

def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value)


class _ForecastBase(_Base, SensorEntity):
    """Basis der Prognose-Sensoren: Werte aus coordinator.forecast, bezogen auf jetzt.

    Aktualisiert sich mit jedem Stream-Poll mit, daher springen jetzt/+15/+30
    pünktlich ins nächste Intervall, ohne dass die Prognose neu geladen wird.
    """

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.forecast is not None

    @property
    def _forecast(self) -> PvForecast | None:
        return self.coordinator.forecast


class ForecastPowerSensor(_ForecastBase):
    """Prognostizierte PV-Leistung jetzt bzw. in N Minuten (W), mit min/max-Band."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 0
    # Die Zeitreihe ändert sich alle 15 Minuten — nicht in den Recorder schreiben
    _unrecorded_attributes = frozenset({"forecast"})

    def __init__(
        self,
        coordinator: SolarmanagerCoordinator,
        key: str,
        translation_key: str,
        offset_minutes: int,
        *,
        with_series: bool = False,
    ) -> None:
        super().__init__(coordinator, key, translation_key)
        self._offset = timedelta(minutes=offset_minutes)
        self._with_series = with_series

    def _at(self) -> datetime:
        return dt_util.utcnow() + self._offset

    @property
    def native_value(self) -> float | None:
        f = self._forecast
        return _rounded(f.power_at(self._at())) if f else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        f = self._forecast
        if f is None:
            return None
        at = self._at()
        attrs: dict[str, Any] = {
            "min": _rounded(f.power_at(at, "min")),
            "max": _rounded(f.power_at(at, "max")),
        }
        if self._with_series:
            now = dt_util.utcnow()
            attrs["forecast"] = [
                {
                    "t": dt_util.as_local(p.start).isoformat(),
                    "w": _rounded(p.watts),
                    "min": _rounded(p.watts_min),
                    "max": _rounded(p.watts_max),
                }
                for p in f.points_between(now, now + timedelta(hours=FORECAST_SERIES_HOURS))
            ]
        return attrs


class ForecastEnergySensor(_ForecastBase):
    """Prognostizierte PV-Energie (Wh) für heute, den Rest von heute oder morgen.

    Bewusst ohne state_class: eine Prognose ist kein Zähler und gehört nicht in
    die Langzeitstatistik. Das Attribut ``complete`` sagt, ob die Zeitreihe das
    ganze Zeitfenster abdeckt (z. B. nach einem Neustart fehlen bei „heute“ die
    bereits vergangenen Intervalle, falls die API nur ab jetzt liefert).
    """

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    _attr_suggested_display_precision = 0

    def __init__(
        self,
        coordinator: SolarmanagerCoordinator,
        key: str,
        translation_key: str,
        window: str,
    ) -> None:
        super().__init__(coordinator, key, translation_key)
        self._window = window

    def _bounds(self) -> tuple[datetime, datetime]:
        now = dt_util.utcnow()
        if self._window == "tomorrow":
            return local_day_bounds(now, 1)
        start, end = local_day_bounds(now)
        if self._window == "remaining_today":
            start = now
        return start, end

    @property
    def native_value(self) -> float | None:
        f = self._forecast
        if f is None:
            return None
        return _rounded(f.energy_between(*self._bounds()))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        f = self._forecast
        if f is None:
            return None
        start, end = self._bounds()
        return {
            "min": _rounded(f.energy_between(start, end, "min")),
            "max": _rounded(f.energy_between(start, end, "max")),
            "complete": f.covers(start, end),
        }


class _ForecastPeakBase(_ForecastBase):
    """Intervall mit der höchsten prognostizierten Leistung heute."""

    def _peak(self) -> ForecastPoint | None:
        f = self._forecast
        if f is None:
            return None
        peak = f.peak_between(*local_day_bounds(dt_util.utcnow()))
        # Ohne Produktion (Nacht, Schnee) gibt es keine sinnvolle Spitze
        return peak if peak is not None and peak.watts > 0 else None


class ForecastPeakPowerSensor(_ForecastPeakBase):
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: SolarmanagerCoordinator) -> None:
        super().__init__(coordinator, "forecast_peak_power_today", "forecast_peak_power_today")

    @property
    def native_value(self) -> float | None:
        peak = self._peak()
        return _rounded(peak.watts) if peak else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        peak = self._peak()
        if peak is None:
            return None
        return {
            "time": dt_util.as_local(peak.start).isoformat(),
            "min": _rounded(peak.watts_min),
            "max": _rounded(peak.watts_max),
        }


class ForecastPeakTimeSensor(_ForecastPeakBase):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: SolarmanagerCoordinator) -> None:
        super().__init__(coordinator, "forecast_peak_time_today", "forecast_peak_time_today")

    @property
    def native_value(self) -> datetime | None:
        peak = self._peak()
        return peak.start if peak else None


# ---------- Geräte-Sensoren ----------

class _DeviceBase(CoordinatorEntity[SolarmanagerCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SolarmanagerCoordinator,
        dev_id: str,
        key: str,
        translation_key: str,
    ):
        super().__init__(coordinator)
        self._dev_id = dev_id
        self._key = key
        self._attr_translation_key = translation_key
        self._attr_unique_id = f"{coordinator.entry.entry_id}_dev_{dev_id}_{key}"

    # device_info dynamisch, damit die „Geräte“-Kachel den Namen aus den
    # Metadaten übernimmt, sobald sie geladen sind
    @property
    def device_info(self) -> dict[str, Any] | None:
        return child_device_info(self.coordinator, self._dev_id)

    @property
    def available(self) -> bool:
        return super().available and self._dev() is not None

    def _dev(self) -> dict[str, Any] | None:
        return find_device(self.coordinator.data, self._dev_id)

    def _dev_value(self) -> Any:
        d = self._dev()
        return d.get(self._key) if d else None


class DevicePowerSensor(_DeviceBase):
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, coordinator: SolarmanagerCoordinator, dev_id: str):
        super().__init__(coordinator, dev_id, "power", "power")

    @property
    def native_value(self) -> float | None:
        v = self._dev_value()
        try:
            return float(v) if v is not None else None
        except Exception:
            return None


class DeviceSocSensor(_DeviceBase):
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator: SolarmanagerCoordinator, dev_id: str):
        super().__init__(coordinator, dev_id, "soc", "soc")

    @property
    def native_value(self) -> float | None:
        v = self._dev_value()
        try:
            return float(v) if v is not None else None
        except Exception:
            return None


class DeviceTemperatureSensor(_DeviceBase):
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator: SolarmanagerCoordinator, dev_id: str):
        super().__init__(coordinator, dev_id, "temperature", "temperature")

    @property
    def native_value(self) -> float | None:
        v = self._dev_value()
        try:
            return float(v) if v is not None else None
        except Exception:
            return None


class DeviceActiveStateSensor(_DeviceBase):
    """Aktivstatus: 1 = an/lädt, 0 = aus, -1 = entlädt."""
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: SolarmanagerCoordinator, dev_id: str):
        super().__init__(coordinator, dev_id, "activeDevice", "active_state")

    @property
    def native_value(self) -> int | None:
        v = self._dev_value()
        try:
            return int(v) if v is not None else None
        except Exception:
            return None


class DeviceDailyEnergySensor(_DeviceBase):
    """Tageszähler (Wh), startet um Mitternacht wieder bei 0.

    Das Quellfeld (iWhTotal/eWhTotal) ist ein kumulativer Zählerstand; den
    Tageswert dazu berechnet der Coordinator (siehe DEVICE_DAILY_KEYS).
    """

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    _attr_suggested_display_precision = 0

    def __init__(
        self,
        coordinator: SolarmanagerCoordinator,
        dev_id: str,
        key: str,
        translation_key: str,
    ):
        # unique_id bleibt am Quellfeld hängen (bestehende Entitäten behalten
        # ihre Historie), gelesen wird der abgeleitete Tageswert
        super().__init__(coordinator, dev_id, key, translation_key)
        self._value_key = DEVICE_DAILY_KEYS[key]

    @property
    def native_value(self) -> float | None:
        d = self._dev()
        v = d.get(self._value_key) if d else None
        if v is None:
            return None
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return None if f < 0 else f


class DeviceOperationStateSensor(_DeviceBase):
    """Numerischer Betriebszustand (Wärmepumpen-spezifisch)."""
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: SolarmanagerCoordinator, dev_id: str):
        super().__init__(coordinator, dev_id, "operationState", "operation_state")

    @property
    def native_value(self) -> int | None:
        v = self._dev_value()
        try:
            return int(v) if v is not None else None
        except Exception:
            return None


class DeviceSwitchStateSensor(_DeviceBase):
    """Schaltzustand (Smart Plug / Switch / Wallbox)."""
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: SolarmanagerCoordinator, dev_id: str):
        super().__init__(coordinator, dev_id, "switchState", "switch_state")

    @property
    def native_value(self) -> int | None:
        v = self._dev_value()
        try:
            return int(v) if v is not None else None
        except Exception:
            return None


class DeviceHeatingAdjustmentSensor(_DeviceBase):
    """Heizungskorrekturwert."""
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: SolarmanagerCoordinator, dev_id: str):
        super().__init__(coordinator, dev_id, "heatingAdjustment", "heating_adjustment")

    @property
    def native_value(self) -> float | None:
        v = self._dev_value()
        try:
            return float(v) if v is not None else None
        except Exception:
            return None


class DeviceRemainingRangeSensor(_DeviceBase):
    """Restreichweite des EV in km."""
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfLength.KILOMETERS

    def __init__(self, coordinator: SolarmanagerCoordinator, dev_id: str):
        super().__init__(coordinator, dev_id, "remainingRange", "remaining_range")

    @property
    def native_value(self) -> float | None:
        v = self._dev_value()
        try:
            return float(v) if v is not None else None
        except Exception:
            return None
