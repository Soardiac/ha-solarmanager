"""Tests für den PV-Überschuss-Sensor."""
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solarmanager.const import (
    CONF_HOST,
    CONF_MODE,
    CONF_SCHEME,
    DOMAIN,
    MODE_LOCAL,
)
from custom_components.solarmanager.coordinator import SolarmanagerCoordinator
from custom_components.solarmanager.sensor import PvSurplusPowerSensor

HOST = "192.168.1.100"


def _entry(hass) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_MODE: MODE_LOCAL, CONF_HOST: HOST, CONF_SCHEME: "http"},
        unique_id=f"local_{HOST}",
    )
    entry.add_to_hass(hass)
    return entry


async def test_surplus_sensor_computes_from_coordinator_data(hass):
    entry = _entry(hass)
    coord = SolarmanagerCoordinator(hass, entry)
    coord.data = {"pW": 3000.0, "cW": 1200.0, "batW": 500.0}

    sensor = PvSurplusPowerSensor(coord)

    assert sensor.native_value == 1300.0


async def test_surplus_sensor_unknown_without_power_data(hass):
    entry = _entry(hass)
    coord = SolarmanagerCoordinator(hass, entry)
    coord.data = {"pW": None, "cW": 1200.0}

    sensor = PvSurplusPowerSensor(coord)

    assert sensor.native_value is None


async def test_surplus_sensor_treats_missing_battery_power_as_zero(hass):
    entry = _entry(hass)
    coord = SolarmanagerCoordinator(hass, entry)
    coord.data = {"pW": 2000.0, "cW": 500.0}

    sensor = PvSurplusPowerSensor(coord)

    assert sensor.native_value == 1500.0
