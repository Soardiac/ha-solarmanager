# sensor.py
from __future__ import annotations
from typing import Any, Optional

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
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

from .coordinator import SolarmanagerCoordinator
from .entity import child_device_info, find_device, site_device_info

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

# Nur Cloud: vom API berechnete Prozentwerte
STATS_SENSORS_CLOUD = [
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


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    coord: SolarmanagerCoordinator = entry.runtime_data

    # Site-Sensoren
    site_entities: list[SensorEntity] = [
        *(SolarmanagerPowerSensor(coord, key, tkey) for key, tkey in POWER_SENSORS),
        *(SolarmanagerEnergySensor(coord, key, tkey) for key, tkey in ENERGY_SENSORS),
        SocSensor(coord),
        DevicesOverviewSensor(coord),
    ]
    site_entities += [SolarmanagerStatsSensor(coord, *spec) for spec in STATS_SENSORS]
    if not coord.is_local:
        site_entities += [SolarmanagerStatsSensor(coord, *spec) for spec in STATS_SENSORS_CLOUD]
    site_entities += [SolarmanagerStatsSensor(coord, *spec) for spec in GRID_STATS_SENSORS]
    site_entities += [SolarmanagerStatsSensor(coord, *spec) for spec in BAT_STATS_SENSORS]
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
    def native_value(self) -> Optional[float]:
        v = (self.coordinator.data or {}).get(self._key)
        return float(v) if isinstance(v, (int, float)) else None


class SolarmanagerEnergySensor(_Base, SensorEntity):
    """Intervall-Energiewerte (Wh) aus dem Stream — keine Zähler, daher per
    Default aus und ohne ENERGY-device_class (erlaubt kein MEASUREMENT)."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    _attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> Optional[float]:
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
    def native_value(self) -> Optional[float]:
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
    def native_value(self) -> Optional[float]:
        v = (self.coordinator.data or {}).get("soc")
        try:
            return float(v) if v is not None else None
        except Exception:
            return None


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
    def native_value(self) -> Optional[float]:
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
    def native_value(self) -> Optional[float]:
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
    def native_value(self) -> Optional[float]:
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
    def native_value(self) -> Optional[int]:
        v = self._dev_value()
        try:
            return int(v) if v is not None else None
        except Exception:
            return None


class DeviceDailyEnergySensor(_DeviceBase):
    """Tageszähler (Wh), akkumuliert seit Mitternacht."""
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR

    @property
    def native_value(self) -> Optional[float]:
        v = self._dev_value()
        try:
            f = float(v) if v is not None else None
            return None if f is not None and f < 0 else f
        except Exception:
            return None


class DeviceOperationStateSensor(_DeviceBase):
    """Numerischer Betriebszustand (Wärmepumpen-spezifisch)."""
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: SolarmanagerCoordinator, dev_id: str):
        super().__init__(coordinator, dev_id, "operationState", "operation_state")

    @property
    def native_value(self) -> Optional[int]:
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
    def native_value(self) -> Optional[int]:
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
    def native_value(self) -> Optional[float]:
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
    def native_value(self) -> Optional[float]:
        v = self._dev_value()
        try:
            return float(v) if v is not None else None
        except Exception:
            return None
