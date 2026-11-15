# number.py
from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import PRIORITY_DEVICE_TYPES
from .coordinator import SolarmanagerCoordinator
from .entity import child_device_info

PARALLEL_UPDATES = 1

# ---------------------------------------------------------------------------
# Batterie Eco-Limits (bestehend, eigene Klasse für Rückwärtskompatibilität)
# (api_field, translation_key)
# ---------------------------------------------------------------------------

ECO_FIELDS = [
    ("dischargeSocLimit", "eco_discharge_soc_limit"),
    ("morningSocLimit", "eco_morning_soc_limit"),
    ("chargingSocLimit", "eco_charging_soc_limit"),
]

# ---------------------------------------------------------------------------
# Geräteparameter-Konfig
#
# key          : Feldname im API-Payload und in raw.data
# tkey         : translation_key (Name via translations/*.json, Icon via icons.json)
# unit         : Einheit
# min/max/step : Wertebereich
# put_method   : Methode auf SolarmanagerCloud
# carry_fields : Felder aus raw.data, die im Payload mitgesendet werden müssen
# value_type   : "float" für Dezimalwerte (Standard: int)
# ---------------------------------------------------------------------------

_INVERTER_NUMBERS = [
    {
        "key": "activePowerLimit",
        "tkey": "active_power_limit",
        "unit": PERCENTAGE,
        "min": 0, "max": 100, "step": 1,
        "put_method": "put_inverter_settings",
        "carry_fields": [],
    },
]

_CAR_CHARGER_NUMBERS = [
    {
        "key": "constantCurrentSetting",
        "tkey": "constant_current",
        "unit": UnitOfElectricCurrent.AMPERE,
        "min": 6, "max": 32, "step": 1,
        "put_method": "put_car_charger_mode",
        "carry_fields": ["chargingMode"],
    },
    {
        "key": "chargingTargetSoc",
        "tkey": "charging_target_soc",
        "unit": PERCENTAGE,
        "min": 0, "max": 100, "step": 1,
        "put_method": "put_car_charger_mode",
        "carry_fields": ["chargingMode"],
    },
    {
        "key": "chargingTargetSocMax",
        "tkey": "charging_target_soc_max",
        "unit": PERCENTAGE,
        "min": 0, "max": 100, "step": 1,
        "put_method": "put_car_charger_mode",
        "carry_fields": ["chargingMode"],
    },
    {
        "key": "minimumChargeQuantityTargetAmount",
        "tkey": "min_charge_quantity_target",
        "unit": UnitOfEnergy.KILO_WATT_HOUR,
        "min": 1, "max": 100, "step": 1,
        "put_method": "put_car_charger_mode",
        "carry_fields": ["chargingMode"],
    },
    {
        "key": "minimumChargeQuantityTargetAmountMax",
        "tkey": "min_charge_quantity_target_max",
        "unit": UnitOfEnergy.KILO_WATT_HOUR,
        "min": 0, "max": 100, "step": 1,
        "put_method": "put_car_charger_mode",
        "carry_fields": ["chargingMode"],
    },
]

_WATER_HEATER_NUMBERS = [
    {
        "key": "powerSettingPercent",
        "tkey": "power_setting_percent",
        "unit": PERCENTAGE,
        "min": 0, "max": 100, "step": 1,
        "put_method": "put_water_heater_mode",
        "carry_fields": [],
    },
]

_BATTERY_NUMBERS = [
    # Stufe 3a – allgemeine SOC-Grenzen
    {
        "key": "upperSocLimit",
        "tkey": "upper_soc_limit",
        "unit": PERCENTAGE,
        "min": 0, "max": 100, "step": 1,
        "put_method": "put_battery_settings",
        "carry_fields": [],
    },
    {
        "key": "lowerSocLimit",
        "tkey": "lower_soc_limit",
        "unit": PERCENTAGE,
        "min": 0, "max": 100, "step": 1,
        "put_method": "put_battery_settings",
        "carry_fields": [],
    },
    # Stufe 3b – Peak-Shaving
    {
        "key": "peakShavingMaxGridPower",
        "tkey": "peak_shaving_max_grid_power",
        "unit": UnitOfPower.WATT,
        "min": 0, "max": 20000, "step": 100,
        "put_method": "put_battery_settings",
        "carry_fields": [],
    },
    {
        "key": "peakShavingRechargePower",
        "tkey": "peak_shaving_recharge_power",
        "unit": UnitOfPower.WATT,
        "min": 0, "max": 20000, "step": 100,
        "put_method": "put_battery_settings",
        "carry_fields": [],
    },
    {
        "key": "peakShavingSocDischargeLimit",
        "tkey": "peak_shaving_soc_discharge_limit",
        "unit": PERCENTAGE,
        "min": 0, "max": 100, "step": 1,
        "put_method": "put_battery_settings",
        "carry_fields": [],
    },
    {
        "key": "peakShavingSocMaxLimit",
        "tkey": "peak_shaving_soc_max_limit",
        "unit": PERCENTAGE,
        "min": 0, "max": 100, "step": 1,
        "put_method": "put_battery_settings",
        "carry_fields": [],
    },
    # Stufe 3b – Manuell
    {
        "key": "maxChargePower",
        "tkey": "max_charge_power",
        "unit": UnitOfPower.WATT,
        "min": 0, "max": 20000, "step": 100,
        "put_method": "put_battery_settings",
        "carry_fields": [],
    },
    {
        "key": "maxDischargePower",
        "tkey": "max_discharge_power",
        "unit": UnitOfPower.WATT,
        "min": 0, "max": 20000, "step": 100,
        "put_method": "put_battery_settings",
        "carry_fields": [],
    },
    # Stufe 3b – Tarif-Optimiert
    {
        "key": "tariffPriceLimit",
        "tkey": "tariff_price_limit",
        "unit": "CHF/kWh",
        "min": 0.0, "max": 2.0, "step": 0.01,
        "value_type": "float",
        "put_method": "put_battery_settings",
        "carry_fields": [],
    },
    {
        "key": "tariffPriceLimitSocMax",
        "tkey": "tariff_price_limit_soc_max",
        "unit": PERCENTAGE,
        "min": 0, "max": 100, "step": 1,
        "put_method": "put_battery_settings",
        "carry_fields": [],
    },
]

DEVICE_NUMBER_CONFIG: dict[str, list[dict]] = {
    # Wechselrichter
    "inverter": _INVERTER_NUMBERS,
    # Wallbox (Keys wie DEVICE_MODE_CONFIG in select.py; "car" = Fahrzeug)
    "carcharging": _CAR_CHARGER_NUMBERS,   # v3 + lokal (bestätigt)
    "carcharger": _CAR_CHARGER_NUMBERS,
    "wallbox": _CAR_CHARGER_NUMBERS,
    # Warmwasser
    "waterheater": _WATER_HEATER_NUMBERS,
    # Batterie
    "battery": _BATTERY_NUMBERS,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coord: SolarmanagerCoordinator = entry.runtime_data
    # Schreiben geht nur über die Cloud (Cloud-Modus oder lokal mit Cloud-Ergänzung)
    if not coord.can_write:
        return

    created: set[str] = set()

    @callback
    def _sync_devices() -> None:
        new_entities: list[NumberEntity] = []
        for dev_id, meta in coord.device_meta.items():
            dev_type = (meta.get("type") or "").lower()

            # Bestehende Batterie-Eco-Limits (eigene Klasse, unique_ids bleiben stabil)
            if dev_type == "battery":
                for key, tkey in ECO_FIELDS:
                    uid = f"{dev_id}_eco_{key}"
                    if uid not in created:
                        created.add(uid)
                        new_entities.append(BatteryEcoNumber(coord, dev_id, key, tkey))

            # Generische Geräteparameter
            for cfg in DEVICE_NUMBER_CONFIG.get(dev_type, []):
                uid = f"{dev_id}_{cfg['key']}"
                if uid not in created:
                    created.add(uid)
                    new_entities.append(DeviceNumberEntity(coord, dev_id, cfg))

            # Priorität in der Verbraucher-Liste (typunabhängig)
            if is_priority_device(meta):
                uid = f"{dev_id}_priority"
                if uid not in created:
                    created.add(uid)
                    new_entities.append(DevicePriorityNumber(coord, dev_id))
        if new_entities:
            async_add_entities(new_entities, True)

    _sync_devices()
    entry.async_on_unload(coord.async_add_listener(_sync_devices))


# ---------------------------------------------------------------------------
# Bestehende Batterie-Eco-Klasse (unique_ids unverändert)
# ---------------------------------------------------------------------------

class BatteryEcoNumber(CoordinatorEntity[SolarmanagerCoordinator], NumberEntity):
    _attr_has_entity_name = True
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(
        self,
        coordinator: SolarmanagerCoordinator,
        dev_id: str,
        field: str,
        translation_key: str,
    ) -> None:
        super().__init__(coordinator)
        self._dev_id = dev_id
        self._field = field
        self._attr_unique_id = f"{coordinator.entry.entry_id}_bat_{dev_id}_{field}"
        self._attr_translation_key = translation_key

    @property
    def device_info(self) -> dict[str, Any]:
        return child_device_info(self.coordinator, self._dev_id)

    @property
    def native_value(self) -> float | None:
        meta = self.coordinator.device_meta.get(self._dev_id) or {}
        v = (meta.get("raw") or {}).get("data", {}).get(self._field)
        try:
            return float(v) if v is not None else None
        except Exception:
            return None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_put_battery_merged(
            self._dev_id, {self._field: round(value)}
        )


# ---------------------------------------------------------------------------
# Generische Number-Entität
# ---------------------------------------------------------------------------

class DeviceNumberEntity(CoordinatorEntity[SolarmanagerCoordinator], NumberEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SolarmanagerCoordinator,
        dev_id: str,
        cfg: dict,
    ) -> None:
        super().__init__(coordinator)
        self._dev_id = dev_id
        self._field: str = cfg["key"]
        self._put_method: str = cfg["put_method"]
        self._carry_fields: list[str] = cfg.get("carry_fields", [])
        self._float_value: bool = cfg.get("value_type") == "float"

        self._attr_native_min_value = cfg["min"]
        self._attr_native_max_value = cfg["max"]
        self._attr_native_step = cfg["step"]
        self._attr_native_unit_of_measurement = cfg["unit"]
        self._attr_translation_key = cfg["tkey"]
        self._attr_unique_id = f"{coordinator.entry.entry_id}_dev_{dev_id}_{self._field}"

    @property
    def device_info(self) -> dict[str, Any]:
        return child_device_info(self.coordinator, self._dev_id)

    @property
    def native_value(self) -> float | None:
        meta = self.coordinator.device_meta.get(self._dev_id) or {}
        v = (meta.get("raw") or {}).get("data", {}).get(self._field)
        try:
            return float(v) if v is not None else None
        except Exception:
            return None

    async def async_set_native_value(self, value: float) -> None:
        coerced: Any = round(value, 4) if self._float_value else round(value)

        # Batterie: vollständiges Settings-Objekt schreiben (read-modify-write),
        # damit das Backend keine nicht-gesendeten Felder auf Defaults zurücksetzt.
        if self._put_method == "put_battery_settings":
            await self.coordinator.async_put_battery_merged(
                self._dev_id, {self._field: coerced}
            )
            return

        payload: dict[str, Any] = {self._field: coerced}

        if self._carry_fields:
            raw_data = (
                (self.coordinator.device_meta.get(self._dev_id) or {})
                .get("raw", {})
                .get("data", {})
            )
            for field in self._carry_fields:
                if field in raw_data:
                    payload[field] = raw_data[field]

        await self.coordinator.async_cloud_put(self._put_method, self._dev_id, payload)


# ---------------------------------------------------------------------------
# Priorität in der Verbraucher-Liste (Drag & Drop im Web/App)
#
# Nur steuerbare Verbraucher (PRIORITY_DEVICE_TYPES) stehen in der Liste
# (lückenlos 1…n); Wechselrichter, Smart Meter und Fahrzeuge haben immer
# priority 1 und werden ignoriert. Beim Setzen fügt der Server das Gerät an Position N ein und
# nummeriert die übrigen selbst neu — der anschliessende Meta-Reload
# aktualisiert alle Prio-Entitäten.
# ---------------------------------------------------------------------------

def is_priority_device(meta: dict) -> bool:
    # Typ aus dem Cloud-Rohobjekt: In der Cloud-Ergänzung stammt "type" der
    # Meta aus der lokalen Liste, "priority" aber immer aus der Cloud.
    raw = meta.get("raw") or {}
    return raw.get("type") in PRIORITY_DEVICE_TYPES and raw.get("priority") is not None


class DevicePriorityNumber(CoordinatorEntity[SolarmanagerCoordinator], NumberEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "priority"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 1
    _attr_native_step = 1

    def __init__(self, coordinator: SolarmanagerCoordinator, dev_id: str) -> None:
        super().__init__(coordinator)
        self._dev_id = dev_id
        self._attr_unique_id = f"{coordinator.entry.entry_id}_dev_{dev_id}_priority"

    @property
    def device_info(self) -> dict[str, Any]:
        return child_device_info(self.coordinator, self._dev_id)

    @property
    def native_max_value(self) -> float:
        count = sum(1 for m in self.coordinator.device_meta.values() if is_priority_device(m))
        return max(count, 1)

    @property
    def native_value(self) -> float | None:
        raw = (self.coordinator.device_meta.get(self._dev_id) or {}).get("raw") or {}
        try:
            return float(raw["priority"])
        except (KeyError, TypeError, ValueError):
            return None

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_cloud_put(
            "put_sensor_priority", self._dev_id, {"priority": round(value)}
        )
