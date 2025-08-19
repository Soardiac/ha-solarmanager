from __future__ import annotations
from typing import Any, Optional

from homeassistant.components.number import NumberEntity
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, CONF_SM_ID
from .coordinator import SolarmanagerCoordinator

FIELDS = [
    ("dischargeSocLimit", "Eco Entlade-Limit"),
    ("morningSocLimit",   "Eco Morgen-Limit"),
    ("chargingSocLimit",  "Eco Lade-Limit"),
]

MIN_PCT = 0
MAX_PCT = 100
STEP   = 1


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    coord: SolarmanagerCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[NumberEntity] = []
    # Batterie-Geräte finden aus device_meta (type == "Battery")
    for dev_id, meta in coord.device_meta.items():
        if (meta.get("type") or "").lower() != "battery":
            continue
        for key, label in FIELDS:
            entities.append(BatteryEcoNumber(coord, dev_id, key, label))

    async_add_entities(entities, True)


class BatteryEcoNumber(CoordinatorEntity[SolarmanagerCoordinator], NumberEntity):
    _attr_has_entity_name = True
    _attr_native_min_value = MIN_PCT
    _attr_native_max_value = MAX_PCT
    _attr_native_step = STEP
    _attr_native_unit_of_measurement = "%"

    def __init__(self, coordinator: SolarmanagerCoordinator, dev_id: str, field: str, label: str):
        super().__init__(coordinator)
        self._dev_id = dev_id
        self._field = field
        self._label = label

        sm_id = coordinator.entry.data.get(CONF_SM_ID, "unknown")
        short = dev_id[-6:] if len(dev_id) >= 6 else dev_id
        self._attr_unique_id = f"{coordinator.entry.entry_id}_bat_{dev_id}_{field}"
        # Name dynamisch:
        friendly = coordinator.get_device_name(dev_id) or f"Gerät {short}"
        self._attr_name = f"{friendly} {label}"

        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"device_{dev_id}")},
            "name": friendly,
            "manufacturer": "Solarmanager",
            "model": "Battery",
            "via_device": (DOMAIN, f"site_{sm_id}"),
        }

    def _current_meta_value(self) -> Optional[float]:
        meta = self.coordinator.device_meta.get(self._dev_id) or {}
        raw = meta.get("raw") or {}
        data = raw.get("data") or {}
        v = data.get(self._field)
        try:
            return float(v) if v is not None else None
        except Exception:
            return None

    @property
    def native_value(self) -> Optional[float]:
        # Anzeige aus den Meta-Daten (info/sensors.data)
        return self._current_meta_value()

    async def async_set_native_value(self, value: float) -> None:
        # Aktuelle Eco-Limits holen
        eco = await self.coordinator.async_get_battery_eco_settings(self._dev_id)

        # Geändertes Feld überschreiben (als int)
        eco[self._field] = int(round(value))

        # Immer alle drei Felder zusammen setzen, plus batteryMode=1 (Eco)
        payload = {
            "batteryMode": 1,
            "dischargeSocLimit": eco["dischargeSocLimit"],
            "morningSocLimit":   eco["morningSocLimit"],
            "chargingSocLimit":  eco["chargingSocLimit"],
        }

        await self.coordinator.client.put_battery_settings(self._dev_id, payload)
        await self.coordinator.async_refresh_device_meta()

