# binary_sensor.py
from __future__ import annotations
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, CONF_SM_ID
from .coordinator import SolarmanagerCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coord: SolarmanagerCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[BinarySensorEntity] = []
    for dev in (coord.data or {}).get("devices", []):
        dev_id = str(dev.get("_id") or "")
        if not dev_id:
            continue
        if "signal" in dev:
            entities.append(DeviceConnectivitySensor(coord, dev_id))

    async_add_entities(entities, True)


def _find_device(data: dict[str, Any] | None, dev_id: str) -> dict[str, Any] | None:
    for it in (data or {}).get("devices", []) or []:
        if str(it.get("_id")) == dev_id:
            return it
    return None


class DeviceConnectivitySensor(
    CoordinatorEntity[SolarmanagerCoordinator], BinarySensorEntity
):
    """True = verbunden ('connected'), False = getrennt."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: SolarmanagerCoordinator, dev_id: str) -> None:
        super().__init__(coordinator)
        self._dev_id = dev_id
        short = dev_id[-6:] if len(dev_id) >= 6 else dev_id
        self._attr_name = f"Gerät {short} Verbindung"
        self._attr_unique_id = f"{coordinator.entry.entry_id}_dev_{dev_id}_signal"

    @property
    def name(self) -> str:
        friendly = (
            self.coordinator.get_device_name(self._dev_id)
            if hasattr(self.coordinator, "get_device_name")
            else None
        )
        if friendly:
            return f"{friendly} Verbindung"
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
    def is_on(self) -> bool | None:
        dev = _find_device(self.coordinator.data, self._dev_id)
        if dev is None:
            return None
        return dev.get("signal") == "connected"
