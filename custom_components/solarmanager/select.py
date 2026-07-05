# select.py
from __future__ import annotations
import time
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import SolarmanagerCoordinator
from .entity import child_device_info

PARALLEL_UPDATES = 1

# Wie lange nach einem PUT der optimistisch gesetzte Wert Vorrang vor einem
# (möglicherweise noch veralteten) API-Wert hat
OPTIMISTIC_TTL = 30  # Sekunden

# ---------------------------------------------------------------------------
# Konfig-Objekte für Hauptmodi (ein Select pro Gerät)
# key  = device.type.lower() aus /v1/info/sensors
# ---------------------------------------------------------------------------

_BATTERY_CFG = {
    "tkey": "battery_mode",
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
    "tkey": "wallbox_mode",
    "options": {
        "0": "Immer laden",
        "1": "Nur Solar",
        "2": "Solar & Tarif",
        "3": "Nie laden",
        "4": "Konstanter Strom",
        "5": "Minimal & Solar",
        "6": "Ladeziel (kWh)",
        "7": "Ladeziel (SoC)",
        "8": "Aria",
    },
    "api_key": "chargingMode",
    "put_method": "put_car_charger_mode",
}

_V2X_CFG = {
    "tkey": "v2x_mode",
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
    "tkey": "heat_pump_mode",
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
    "tkey": "water_heater_mode",
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
    "tkey": "smart_plug_mode",
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
    "tkey": "switch_mode",
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
    "tkey": "battery_manual_mode",
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
    coord: SolarmanagerCoordinator = entry.runtime_data
    if coord.is_local:
        return

    created: set[str] = set()

    @callback
    def _sync_devices() -> None:
        new_entities: list[SelectEntity] = []
        for dev_id, meta in coord.device_meta.items():
            dev_type = (meta.get("type") or "").lower()

            if dev_type in DEVICE_MODE_CONFIG:
                uid = f"{dev_id}_mode"
                if uid not in created:
                    created.add(uid)
                    new_entities.append(DeviceModeSelect(coord, dev_id, dev_type))

            for cfg in DEVICE_EXTRA_SELECTS.get(dev_type, []):
                uid = f"{dev_id}_{cfg['api_key']}"
                if uid not in created:
                    created.add(uid)
                    new_entities.append(DeviceExtraSelect(coord, dev_id, cfg))
        if new_entities:
            async_add_entities(new_entities, True)

    _sync_devices()
    entry.async_on_unload(coord.async_add_listener(_sync_devices))


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
        translation_key: str,
        uid_suffix: str,
    ) -> None:
        super().__init__(coordinator)
        self._dev_id = dev_id
        self._api_key = api_key
        self._put_method = put_method
        self._options_map = options_map
        self._attr_options = list(options_map.values())
        self._attr_translation_key = translation_key
        self._attr_unique_id = f"{coordinator.entry.entry_id}_dev_{dev_id}_{uid_suffix}"

        # Optimistischer Zustand nach einem PUT (Backend liefert kurzzeitig
        # noch den alten Wert) — verfällt nach OPTIMISTIC_TTL Sekunden
        self._optimistic: str | None = None
        self._optimistic_until: float = 0.0

    @property
    def device_info(self) -> dict[str, Any]:
        return child_device_info(self.coordinator, self._dev_id)

    def _api_label(self) -> str | None:
        """Aktuellen API-Wert auf das Options-Label mappen."""
        meta = self.coordinator.device_meta.get(self._dev_id) or {}
        raw_data = (meta.get("raw") or {}).get("data") or {}
        val = raw_data.get(self._api_key)
        if val is None:
            return None
        try:
            return self._options_map.get(str(int(val)))
        except (ValueError, TypeError):
            return None

    @callback
    def _handle_coordinator_update(self) -> None:
        # Optimistischen Wert freigeben, sobald das Backend nachgezogen hat
        # oder die Karenzzeit abgelaufen ist
        if self._optimistic is not None:
            api_label = self._api_label()
            if api_label == self._optimistic or (
                api_label is not None and time.time() > self._optimistic_until
            ):
                self._optimistic = None
        super()._handle_coordinator_update()

    @property
    def current_option(self) -> str | None:
        if self._optimistic is not None and time.time() <= self._optimistic_until:
            return self._optimistic
        return self._api_label() or self._optimistic

    async def async_select_option(self, option: str) -> None:
        val = next((k for k, v in self._options_map.items() if v == option), None)
        if val is None:
            return
        # Batterie: vollständiges Settings-Objekt schreiben (read-modify-write),
        # damit das Backend keine nicht-gesendeten Felder auf Defaults zurücksetzt.
        if self._put_method == "put_battery_settings":
            await self.coordinator.async_put_battery_merged(
                self._dev_id, {self._api_key: int(val)}
            )
        else:
            payload = {self._api_key: int(val)}
            put_fn = getattr(self.coordinator.client, self._put_method)
            await put_fn(self._dev_id, payload)
            await self.coordinator.async_refresh_device_meta()
        self._optimistic = option
        self._optimistic_until = time.time() + OPTIMISTIC_TTL
        self.async_write_ha_state()


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
            cfg["api_key"], cfg["put_method"], cfg["options"], cfg["tkey"],
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
            cfg["api_key"], cfg["put_method"], cfg["options"], cfg["tkey"],
            cfg["api_key"],
        )
