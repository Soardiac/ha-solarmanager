# datetime.py
from __future__ import annotations
from datetime import datetime
from typing import Any

from homeassistant.components.datetime import DateTimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .coordinator import SolarmanagerCoordinator
from .entity import child_device_info

PARALLEL_UPDATES = 1

_CAR_CHARGER_DATETIMES = [
    {
        "key": "chargingTargetSocDateTime",
        "tkey": "charging_target_soc_datetime",
        "put_method": "put_car_charger_mode",
        "carry_fields": ["chargingMode"],
    },
    {
        "key": "minimumChargeQuantityTargetDateTime",
        "tkey": "min_charge_quantity_datetime",
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
    if coord.is_local:
        return

    created: set[str] = set()

    @callback
    def _sync_devices() -> None:
        new_entities: list[DateTimeEntity] = []
        for dev_id, meta in coord.device_meta.items():
            dev_type = (meta.get("type") or "").lower()
            for cfg in DEVICE_DATETIME_CONFIG.get(dev_type, []):
                uid = f"{dev_id}_{cfg['key']}"
                if uid not in created:
                    created.add(uid)
                    new_entities.append(DeviceDateTimeEntity(coord, dev_id, cfg))
        if new_entities:
            async_add_entities(new_entities, True)

    _sync_devices()
    entry.async_on_unload(coord.async_add_listener(_sync_devices))


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
        self._put_method: str = cfg["put_method"]
        self._carry_fields: list[str] = cfg.get("carry_fields", [])

        self._attr_translation_key = cfg["tkey"]
        self._attr_unique_id = f"{coordinator.entry.entry_id}_dev_{dev_id}_{self._field}"

    @property
    def device_info(self) -> dict[str, Any]:
        return child_device_info(self.coordinator, self._dev_id)

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
        iso = dt_util.as_utc(value).strftime("%Y-%m-%dT%H:%M:%S.000Z")
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
