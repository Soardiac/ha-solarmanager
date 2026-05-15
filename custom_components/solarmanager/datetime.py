from __future__ import annotations
from datetime import datetime
from typing import Any

from homeassistant.components.datetime import DateTimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, CONF_SM_ID
from .coordinator import SolarmanagerCoordinator

PARALLEL_UPDATES = 1

_CAR_CHARGER_DATETIMES = [
    {
        "key": "chargingTargetSocDateTime",
        "label": "Ladeziel SOC Termin",
        "put_method": "put_car_charger_mode",
        "carry_fields": ["chargingMode"],
    },
    {
        "key": "minimumChargeQuantityTargetDateTime",
        "label": "Ladeziel kWh Termin",
        "put_method": "put_car_charger_mode",
        "carry_fields": ["chargingMode"],
    },
]

DEVICE_DATETIME_CONFIG: dict[str, list[dict]] = {
    "car": _CAR_CHARGER_DATETIMES,
    "car charger": _CAR_CHARGER_DATETIMES,
    "carcharger": _CAR_CHARGER_DATETIMES,
    "carcharging": _CAR_CHARGER_DATETIMES,
    "car charging": _CAR_CHARGER_DATETIMES,
    "ocpp charger": _CAR_CHARGER_DATETIMES,
    "wallbox": _CAR_CHARGER_DATETIMES,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coord: SolarmanagerCoordinator = entry.runtime_data
    entities: list[DateTimeEntity] = []
    for dev_id, meta in coord.device_meta.items():
        dev_type = (meta.get("type") or "").lower()
        for cfg in DEVICE_DATETIME_CONFIG.get(dev_type, []):
            entities.append(DeviceDateTimeEntity(coord, dev_id, cfg))
    async_add_entities(entities, True)


class DeviceDateTimeEntity(CoordinatorEntity[SolarmanagerCoordinator], DateTimeEntity):
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
    def native_value(self) -> datetime | None:
        meta = self.coordinator.device_meta.get(self._dev_id) or {}
        v = (meta.get("raw") or {}).get("data", {}).get(self._field)
        if v is None:
            return None
        try:
            return dt_util.parse_datetime(str(v))
        except Exception:
            return None

    async def async_set_value(self, value: datetime) -> None:
        iso = value.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        payload: dict[str, Any] = {self._field: iso}
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
