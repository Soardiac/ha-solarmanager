# select.py
from __future__ import annotations
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, CONF_SM_ID
from .coordinator import SolarmanagerCoordinator

# ---------------------------------------------------------------------------
# Konfig-Objekte für Hauptmodi (ein Select pro Gerät)
# key  = device.type.lower() aus /v1/info/sensors
# ---------------------------------------------------------------------------

_BATTERY_CFG = {
    "label": "Batterie Modus",
    "options": {
        "0": "Standard",
        "1": "Eco",
        "2": "Peak-Shaving",
        "3": "Manuell",
        "4": "Tarif-Optimiert",
        "5": "Standard (aktiv)",
        "6": "KI-Optimierung",
    },
    "api_key": "batteryMode",
    "put_method": "put_battery_settings",
}

_CAR_CHARGER_CFG = {
    "label": "Wallbox Modus",
    "options": {
        "0": "Schnellladen",
        "1": "Nur Solar",
        "2": "Solar & Tarif",
        "3": "Nicht laden",
        "4": "Konstantstrom",
        "5": "Minimal & Solar",
        "6": "Mindestmenge",
        "7": "Ladziel (%)",
    },
    "api_key": "chargingMode",
    "put_method": "put_car_charger_mode",
}

_V2X_CFG = {
    "label": "V2X Modus",
    "options": {
        "0": "Immer laden",
        "1": "Solar-Optimiert",
        "2": "Solar & Tarif",
        "3": "Manuell",
        "4": "Ziel-SOC",
    },
    "api_key": "v2xChargingMode",
    "put_method": "put_v2x_mode",
}

_HEAT_PUMP_CFG = {
    "label": "Wärmepumpe Modus",
    "options": {
        "0": "Kein Modus",
        "1": "EIN",
        "2": "AUS",
        "3": "Nur Solar",
        "4": "Solar & Tarif",
        "5": "Keine Steuerung",
        "6": "Normalbetrieb",
        "7": "OEM 14",
        "8": "KI-Optimierung",
    },
    "api_key": "heatPumpChargingMode",
    "put_method": "put_heat_pump_mode",
}

_WATER_HEATER_CFG = {
    "label": "Warmwasser Modus",
    "options": {
        "1": "EIN",
        "2": "AUS",
        "3": "Nur Solar",
        "4": "Solar & Tarif",
        "5": "Keine Steuerung",
        "6": "ECO",
        "7": "KI-Optimierung",
    },
    "api_key": "waterHeaterMode",
    "put_method": "put_water_heater_mode",
}

_SMART_PLUG_CFG = {
    "label": "Smart Plug Modus",
    "options": {
        "1": "EIN",
        "2": "AUS",
        "3": "Nur Solar",
        "4": "Solar & Tarif",
        "5": "Keine Steuerung",
    },
    "api_key": "chargingMode",
    "put_method": "put_smart_plug_mode",
}

_SWITCH_CFG = {
    "label": "Schalter Modus",
    "options": {
        "0": "Kein Modus",
        "1": "EIN",
        "2": "AUS",
        "3": "Nur Solar",
        "4": "Solar & Tarif",
        "5": "Keine Steuerung",
    },
    "api_key": "chargingMode",
    "put_method": "put_switch_mode",
}

# Haupt-Modus pro Gerätetyp (unique_id endet auf "_mode")
DEVICE_MODE_CONFIG: dict[str, dict] = {
    # Batterie
    "battery": _BATTERY_CFG,
    # Wallbox / Car Charger (alle bekannten Typ-Varianten)
    "car": _CAR_CHARGER_CFG,           # Cloud-API (bestätigt)
    "car charger": _CAR_CHARGER_CFG,
    "carcharger": _CAR_CHARGER_CFG,
    "carcharging": _CAR_CHARGER_CFG,   # lokale API (bestätigt)
    "car charging": _CAR_CHARGER_CFG,  # Cloud-API (bestätigt)
    "ocpp charger": _CAR_CHARGER_CFG,
    "wallbox": _CAR_CHARGER_CFG,
    # V2X
    "v2x": _V2X_CFG,
    "v2x charger": _V2X_CFG,
    "v2xcharger": _V2X_CFG,
    # Wärmepumpe
    "heat pump": _HEAT_PUMP_CFG,
    "heatpump": _HEAT_PUMP_CFG,        # lokale API (bestätigt)
    "sg ready switch": _HEAT_PUMP_CFG,
    # Warmwasser
    "water heater": _WATER_HEATER_CFG,
    "waterheater": _WATER_HEATER_CFG,
    # Smart Plug
    "smart plug": _SMART_PLUG_CFG,
    "smartplug": _SMART_PLUG_CFG,
    # Switch
    "switch": _SWITCH_CFG,
}

# ---------------------------------------------------------------------------
# Zusätzliche Selects pro Gerätetyp (Stufe 3b)
# unique_id endet auf "_{api_key}"
# ---------------------------------------------------------------------------

_BATTERY_MANUAL_MODE_CFG = {
    "label": "Manuell Richtung",
    "options": {
        "0": "Laden",
        "1": "Entladen",
        "2": "AUS",
    },
    "api_key": "batteryManualMode",
    "put_method": "put_battery_settings",
}

DEVICE_EXTRA_SELECTS: dict[str, list[dict]] = {
    "battery": [_BATTERY_MANUAL_MODE_CFG],
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coord: SolarmanagerCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SelectEntity] = []
    for dev_id, meta in coord.device_meta.items():
        dev_type = (meta.get("type") or "").lower()

        if dev_type in DEVICE_MODE_CONFIG:
            entities.append(DeviceModeSelect(coord, dev_id, dev_type))

        for cfg in DEVICE_EXTRA_SELECTS.get(dev_type, []):
            entities.append(DeviceExtraSelect(coord, dev_id, cfg))

    async_add_entities(entities, True)


# ---------------------------------------------------------------------------
# Gemeinsame Basis
# ---------------------------------------------------------------------------

class _DeviceSelectBase(CoordinatorEntity[SolarmanagerCoordinator], SelectEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SolarmanagerCoordinator,
        dev_id: str,
        api_key: str,
        put_method: str,
        options_map: dict[str, str],
        label: str,
        uid_suffix: str,
    ) -> None:
        super().__init__(coordinator)
        self._dev_id = dev_id
        self._api_key = api_key
        self._put_method = put_method
        self._options_map = options_map
        self._label = label
        self._attr_options = list(options_map.values())
        self._optimistic: str | None = None

        short = dev_id[-6:] if len(dev_id) >= 6 else dev_id
        self._attr_unique_id = f"{coordinator.entry.entry_id}_dev_{dev_id}_{uid_suffix}"
        self._attr_name = f"Gerät {short} {label}"

    @property
    def name(self) -> str:
        friendly = (
            self.coordinator.get_device_name(self._dev_id)
            if hasattr(self.coordinator, "get_device_name")
            else None
        )
        if friendly:
            return f"{friendly} {self._label}"
        return self._attr_name

    @property
    def device_info(self) -> dict[str, Any]:
        sm_id = self.coordinator.entry.data.get(CONF_SM_ID, "unknown")
        friendly = (
            self.coordinator.get_device_name(self._dev_id)
            if hasattr(self.coordinator, "get_device_name")
            else None
        )
        short = self._dev_id[-6:] if len(self._dev_id) >= 6 else self._dev_id
        return {
            "identifiers": {(DOMAIN, f"device_{self._dev_id}")},
            "name": friendly or f"Solarmanager Gerät {short}",
            "manufacturer": "Solarmanager",
            "model": "Stream device",
            "via_device": (DOMAIN, f"site_{sm_id}"),
        }

    @property
    def current_option(self) -> str | None:
        meta = self.coordinator.device_meta.get(self._dev_id) or {}
        raw_data = (meta.get("raw") or {}).get("data") or {}
        val = raw_data.get(self._api_key)
        if val is not None:
            try:
                label = self._options_map.get(str(int(val)))
                if label:
                    return label
            except (ValueError, TypeError):
                pass
        return self._optimistic

    async def async_select_option(self, option: str) -> None:
        val = next((k for k, v in self._options_map.items() if v == option), None)
        if val is None:
            return
        payload = {self._api_key: int(val)}
        put_fn = getattr(self.coordinator.client, self._put_method)
        await put_fn(self._dev_id, payload)
        self._optimistic = option
        await self.coordinator.async_refresh_device_meta()


class DeviceModeSelect(_DeviceSelectBase):
    """Haupt-Betriebsmodus (unique_id endet auf _mode – stabil für Updates)."""

    def __init__(
        self,
        coordinator: SolarmanagerCoordinator,
        dev_id: str,
        dev_type_key: str,
    ) -> None:
        cfg = DEVICE_MODE_CONFIG[dev_type_key]
        super().__init__(
            coordinator, dev_id,
            cfg["api_key"], cfg["put_method"], cfg["options"], cfg["label"],
            "mode",
        )


class DeviceExtraSelect(_DeviceSelectBase):
    """Zusätzliche Select-Parameter (z. B. batteryManualMode)."""

    def __init__(
        self,
        coordinator: SolarmanagerCoordinator,
        dev_id: str,
        cfg: dict,
    ) -> None:
        super().__init__(
            coordinator, dev_id,
            cfg["api_key"], cfg["put_method"], cfg["options"], cfg["label"],
            cfg["api_key"],
        )
