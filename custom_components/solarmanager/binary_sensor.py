# binary_sensor.py
from __future__ import annotations
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import SolarmanagerCoordinator
from .entity import child_device_info, find_device

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coord: SolarmanagerCoordinator = entry.runtime_data

    created: set[str] = set()

    @callback
    def _sync_devices() -> None:
        new_entities: list[BinarySensorEntity] = []
        for dev in (coord.data or {}).get("devices", []) or []:
            dev_id = str(dev.get("_id") or "")
            if not dev_id or dev_id in created:
                continue
            if "signal" in dev:
                created.add(dev_id)
                new_entities.append(DeviceConnectivitySensor(coord, dev_id))
        if new_entities:
            async_add_entities(new_entities, True)

    _sync_devices()
    entry.async_on_unload(coord.async_add_listener(_sync_devices))


class DeviceConnectivitySensor(
    CoordinatorEntity[SolarmanagerCoordinator], BinarySensorEntity
):
    """True = verbunden ('connected'), False = getrennt."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "signal"

    def __init__(self, coordinator: SolarmanagerCoordinator, dev_id: str) -> None:
        super().__init__(coordinator)
        self._dev_id = dev_id
        self._attr_unique_id = f"{coordinator.entry.entry_id}_dev_{dev_id}_signal"

    @property
    def device_info(self) -> dict[str, Any]:
        return child_device_info(self.coordinator, self._dev_id)

    @property
    def is_on(self) -> bool | None:
        dev = find_device(self.coordinator.data, self._dev_id)
        if dev is None:
            return None
        return dev.get("signal") == "connected"
