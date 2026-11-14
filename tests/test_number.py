"""Tests für die Prioritäts-Entität (Prio-Liste der Verbraucher)."""
from unittest.mock import AsyncMock

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solarmanager import number
from custom_components.solarmanager.const import (
    CONF_MODE,
    CONF_SM_ID,
    DOMAIN,
    MODE_CLOUD,
)
from custom_components.solarmanager.coordinator import SolarmanagerCoordinator


def _meta(device_type: str, priority: int | None, typ: str = "Smart Plug") -> dict:
    raw = {"device_type": device_type, "type": typ}
    if priority is not None:
        raw["priority"] = priority
    return {"name": typ, "type": typ, "raw": raw}


DEVICE_META = {
    "inv": _meta("inverter", 1, "Inverter"),
    "meter": _meta("smart-meter", 1, "Smart Meter"),
    "car": _meta("car", 1, "Car"),
    "bat": _meta("device", 1, "Battery"),
    "wallbox": _meta("device", 2, "Car Charging"),
    "plug": _meta("device", 3),
}


async def _setup(hass) -> tuple[SolarmanagerCoordinator, list]:
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_MODE: MODE_CLOUD, CONF_SM_ID: "SM1"}, unique_id="SM1"
    )
    entry.add_to_hass(hass)
    coord = SolarmanagerCoordinator(hass, entry)
    coord.cloud = AsyncMock()
    coord.client = coord.cloud
    coord.data = {}
    coord.device_meta = DEVICE_META
    entry.runtime_data = coord
    entities: list = []
    await number.async_setup_entry(hass, entry, lambda e, update=False: entities.extend(e))
    return coord, [e for e in entities if isinstance(e, number.DevicePriorityNumber)]


async def test_priority_only_for_consumer_devices(hass):
    _, prio = await _setup(hass)
    assert sorted(e._dev_id for e in prio) == ["bat", "plug", "wallbox"]


async def test_priority_value_and_range(hass):
    _, prio = await _setup(hass)
    wallbox = next(e for e in prio if e._dev_id == "wallbox")
    assert wallbox.native_value == 2
    assert wallbox.native_min_value == 1
    assert wallbox.native_max_value == 3


async def test_set_priority_writes_via_cloud_and_reloads(hass):
    coord, prio = await _setup(hass)
    coord.async_refresh_device_meta = AsyncMock()
    plug = next(e for e in prio if e._dev_id == "plug")

    await plug.async_set_native_value(1.0)

    coord.cloud.put_sensor_priority.assert_awaited_once_with("plug", {"priority": 1})
    coord.async_refresh_device_meta.assert_awaited_once()
