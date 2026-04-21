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
# Gerättyp → Steuerungskonfiguration
# key  = device.type.lower() aus /v1/info/sensors
# ---------------------------------------------------------------------------
# options: {str(int_wert): "Anzeigename"}
# api_key: Feldname im PUT-Payload
# put_method: Methode auf SolarmanagerCloud
# ---------------------------------------------------------------------------
DEVICE_MODE_CONFIG: dict[str, dict] = {
    "battery": {
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
    },
    "car charger": {
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
    },
    "v2x": {
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
    },
    "v2x charger": {
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
    },
    "heat pump": {
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
    },
    "water heater": {
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
    },
    "smart plug": {
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
    },
    "switch": {
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
    },
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

    async_add_entities(entities, True)


class DeviceModeSelect(CoordinatorEntity[SolarmanagerCoordinator], SelectEntity):
    """Select-Entität für den Betriebsmodus eines Geräts."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SolarmanagerCoordinator,
        dev_id: str,
        dev_type_key: str,
    ) -> None:
        super().__init__(coordinator)
        self._dev_id = dev_id
        cfg = DEVICE_MODE_CONFIG[dev_type_key]
        self._api_key: str = cfg["api_key"]
        self._put_method: str = cfg["put_method"]
        self._options_map: dict[str, str] = cfg["options"]  # "0" → "Standard"
        self._label: str = cfg["label"]

        self._attr_options = list(cfg["options"].values())

        short = dev_id[-6:] if len(dev_id) >= 6 else dev_id
        self._attr_name = f"Gerät {short} {self._label}"
        self._attr_unique_id = f"{coordinator.entry.entry_id}_dev_{dev_id}_mode"

        # Optimistischer Zustand – wird nach erfolgreichem PUT gesetzt
        self._optimistic: str | None = None

    # --- Dynamischer Name (aus Coordinator-Meta) ---

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

    # --- Aktueller Wert ---

    @property
    def current_option(self) -> str | None:
        # Versuche Wert aus gecachten Device-Metadaten zu lesen
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
        # Fallback: optimistischer Zustand aus letztem SET
        return self._optimistic

    # --- Steuerung ---

    async def async_select_option(self, option: str) -> None:
        # Label → Integerwert
        val = next((k for k, v in self._options_map.items() if v == option), None)
        if val is None:
            return

        payload = {self._api_key: int(val)}
        put_fn = getattr(self.coordinator.client, self._put_method)
        await put_fn(self._dev_id, payload)

        self._optimistic = option
        await self.coordinator.async_refresh_device_meta()
