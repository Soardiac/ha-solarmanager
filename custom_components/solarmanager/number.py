from __future__ import annotations
from typing import Any, Optional

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, CONF_SM_ID
from .coordinator import SolarmanagerCoordinator

# ---------------------------------------------------------------------------
# Batterie Eco-Limits (bestehend, eigene Klasse für Rückwärtskompatibilität)
# ---------------------------------------------------------------------------

ECO_FIELDS = [
    ("dischargeSocLimit", "Eco Entlade-Limit"),
    ("morningSocLimit",   "Eco Morgen-Limit"),
    ("chargingSocLimit",  "Eco Lade-Limit"),
]

# ---------------------------------------------------------------------------
# Generische Geräteparameter (Stufe 3a)
#
# key          : Feldname im API-Payload und in raw.data
# label        : Anzeigename in HA
# unit         : Einheit
# min/max/step : Wertebereich
# put_method   : Methode auf SolarmanagerCloud
# icon         : MDI-Icon
# carry_fields : Felder aus raw.data, die mit in den Payload müssen
#                (z. B. weil die API sie als required deklariert)
# ---------------------------------------------------------------------------

_INVERTER_NUMBERS = [
    {
        "key": "activePowerLimit",
        "label": "Einspeisebegrenzung",
        "unit": "%",
        "min": 0, "max": 100, "step": 1,
        "put_method": "put_inverter_settings",
        "icon": "mdi:transmission-tower-export",
        "carry_fields": [],
    },
]

_CAR_CHARGER_NUMBERS = [
    {
        "key": "constantCurrentSetting",
        "label": "Konstantstrom",
        "unit": "A",
        "min": 6, "max": 32, "step": 1,
        "put_method": "put_car_charger_mode",
        "icon": "mdi:current-ac",
        "carry_fields": ["chargingMode"],
    },
]

_WATER_HEATER_NUMBERS = [
    {
        "key": "powerSettingPercent",
        "label": "Leistung",
        "unit": "%",
        "min": 0, "max": 100, "step": 1,
        "put_method": "put_water_heater_mode",
        "icon": "mdi:water-percent",
        "carry_fields": [],
    },
]

_BATTERY_NUMBERS = [
    {
        "key": "upperSocLimit",
        "label": "SOC-Obergrenze",
        "unit": "%",
        "min": 0, "max": 100, "step": 1,
        "put_method": "put_battery_settings",
        "icon": "mdi:battery-arrow-up-outline",
        "carry_fields": [],
    },
    {
        "key": "lowerSocLimit",
        "label": "SOC-Untergrenze",
        "unit": "%",
        "min": 0, "max": 100, "step": 1,
        "put_method": "put_battery_settings",
        "icon": "mdi:battery-arrow-down-outline",
        "carry_fields": [],
    },
]

DEVICE_NUMBER_CONFIG: dict[str, list[dict]] = {
    # Wechselrichter
    "inverter": _INVERTER_NUMBERS,
    # Wallbox / Car Charger (alle bekannten Typ-Varianten)
    "car": _CAR_CHARGER_NUMBERS,           # Cloud-API (bestätigt)
    "car charger": _CAR_CHARGER_NUMBERS,
    "carcharger": _CAR_CHARGER_NUMBERS,
    "carcharging": _CAR_CHARGER_NUMBERS,   # lokale API (bestätigt)
    "car charging": _CAR_CHARGER_NUMBERS,  # Cloud-API (bestätigt)
    "ocpp charger": _CAR_CHARGER_NUMBERS,
    "wallbox": _CAR_CHARGER_NUMBERS,
    # Warmwasser
    "water heater": _WATER_HEATER_NUMBERS,
    "waterheater": _WATER_HEATER_NUMBERS,
    # Batterie
    "battery": _BATTERY_NUMBERS,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coord: SolarmanagerCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[NumberEntity] = []
    for dev_id, meta in coord.device_meta.items():
        dev_type = (meta.get("type") or "").lower()

        # Bestehende Batterie-Eco-Limits (eigene Klasse, unique_ids bleiben stabil)
        if dev_type == "battery":
            for key, label in ECO_FIELDS:
                entities.append(BatteryEcoNumber(coord, dev_id, key, label))

        # Generische Geräteparameter
        for cfg in DEVICE_NUMBER_CONFIG.get(dev_type, []):
            entities.append(DeviceNumberEntity(coord, dev_id, cfg))

    async_add_entities(entities, True)


# ---------------------------------------------------------------------------
# Bestehende Batterie-Eco-Klasse (unverändert)
# ---------------------------------------------------------------------------

class BatteryEcoNumber(CoordinatorEntity[SolarmanagerCoordinator], NumberEntity):
    _attr_has_entity_name = True
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "%"

    def __init__(
        self,
        coordinator: SolarmanagerCoordinator,
        dev_id: str,
        field: str,
        label: str,
    ) -> None:
        super().__init__(coordinator)
        self._dev_id = dev_id
        self._field = field

        sm_id = coordinator.entry.data.get(CONF_SM_ID, "unknown")
        short = dev_id[-6:] if len(dev_id) >= 6 else dev_id
        self._attr_unique_id = f"{coordinator.entry.entry_id}_bat_{dev_id}_{field}"
        friendly = coordinator.get_device_name(dev_id) or f"Gerät {short}"
        self._attr_name = f"{friendly} {label}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"device_{dev_id}")},
            "name": friendly,
            "manufacturer": "Solarmanager",
            "model": "Battery",
            "via_device": (DOMAIN, f"site_{sm_id}"),
        }

    @property
    def native_value(self) -> Optional[float]:
        meta = self.coordinator.device_meta.get(self._dev_id) or {}
        v = (meta.get("raw") or {}).get("data", {}).get(self._field)
        try:
            return float(v) if v is not None else None
        except Exception:
            return None

    async def async_set_native_value(self, value: float) -> None:
        eco = await self.coordinator.async_get_battery_eco_settings(self._dev_id)
        eco[self._field] = int(round(value))
        payload = {
            "dischargeSocLimit": eco["dischargeSocLimit"],
            "morningSocLimit":   eco["morningSocLimit"],
            "chargingSocLimit":  eco["chargingSocLimit"],
        }
        await self.coordinator.client.put_battery_settings(self._dev_id, payload)
        await self.coordinator.async_refresh_device_meta()


# ---------------------------------------------------------------------------
# Generische Number-Entität für alle anderen Geräteparameter
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
        self._label: str = cfg["label"]
        self._put_method: str = cfg["put_method"]
        self._carry_fields: list[str] = cfg.get("carry_fields", [])

        self._attr_native_min_value = cfg["min"]
        self._attr_native_max_value = cfg["max"]
        self._attr_native_step = cfg["step"]
        self._attr_native_unit_of_measurement = cfg["unit"]
        self._attr_icon = cfg.get("icon")

        sm_id = coordinator.entry.data.get(CONF_SM_ID, "unknown")
        short = dev_id[-6:] if len(dev_id) >= 6 else dev_id
        friendly = coordinator.get_device_name(dev_id) or f"Gerät {short}"
        self._attr_name = f"{friendly} {self._label}"
        self._attr_unique_id = f"{coordinator.entry.entry_id}_dev_{dev_id}_{self._field}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"device_{dev_id}")},
            "name": friendly,
            "manufacturer": "Solarmanager",
            "model": "Stream device",
            "via_device": (DOMAIN, f"site_{sm_id}"),
        }

    @property
    def native_value(self) -> Optional[float]:
        meta = self.coordinator.device_meta.get(self._dev_id) or {}
        v = (meta.get("raw") or {}).get("data", {}).get(self._field)
        try:
            return float(v) if v is not None else None
        except Exception:
            return None

    async def async_set_native_value(self, value: float) -> None:
        payload: dict[str, Any] = {self._field: int(round(value))}

        # Pflichtfelder aus aktuellen Metadaten mitlesen
        if self._carry_fields:
            raw_data = (
                (self.coordinator.device_meta.get(self._dev_id) or {})
                .get("raw", {})
                .get("data", {})
            )
            for field in self._carry_fields:
                if field in raw_data:
                    payload[field] = raw_data[field]

        put_fn = getattr(self.coordinator.client, self._put_method)
        await put_fn(self._dev_id, payload)
        await self.coordinator.async_refresh_device_meta()
