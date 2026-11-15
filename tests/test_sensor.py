"""Tests für den PV-Überschuss-Sensor und die Geräte-Tageszähler."""
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solarmanager.const import (
    CONF_HOST,
    CONF_MODE,
    CONF_SCHEME,
    DOMAIN,
    MODE_LOCAL,
)
from custom_components.solarmanager.coordinator import SolarmanagerCoordinator
from custom_components.solarmanager.sensor import (
    DeviceDailyEnergySensor,
    DeviceMeasurementSensor,
    DeviceUpdatedAtSensor,
    PvSurplusPowerSensor,
    async_setup_entry,
)

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


async def test_device_daily_energy_sensor_reads_derived_daily_value(hass):
    """Der Tagessensor zeigt den abgeleiteten Tageswert, nicht den kumulativen Zähler."""
    entry = _entry(hass)
    coord = SolarmanagerCoordinator(hass, entry)
    coord.data = {"devices": [{"_id": "dev1", "iWhTotal": 12345.0, "iWhToday": 780.0}]}

    sensor = DeviceDailyEnergySensor(coord, "dev1", "iWhTotal", "daily_consumption")

    assert sensor.native_value == 780.0
    assert sensor.unique_id.endswith("_dev_dev1_iWhTotal")


async def test_device_daily_energy_sensor_unknown_before_first_daily_value(hass):
    """Ohne abgeleiteten Tageswert bleibt der Sensor unbekannt (kein Gesamtzähler-Fallback)."""
    entry = _entry(hass)
    coord = SolarmanagerCoordinator(hass, entry)
    coord.data = {"devices": [{"_id": "dev1", "iWhTotal": 12345.0}]}

    sensor = DeviceDailyEnergySensor(coord, "dev1", "iWhTotal", "daily_consumption")

    assert sensor.native_value is None


# ---------- Weitere Gerätemesswerte (Issue #38) ----------

_HEATPUMP = {
    "_id": "hp1",
    "power": 1200.0,
    "temperatureOutside": 4.5,
    "temperatureSupply": 35.0,
    "temperatureReturn": 30.5,
    "updatedAt": "2026-11-15T20:00:00.000Z",
}


async def _setup_device_sensors(hass, devices):
    """async_setup_entry mit gegebenen Geräten ausführen, Geräte-Sensoren zurückgeben."""
    entry = _entry(hass)
    coord = SolarmanagerCoordinator(hass, entry)
    coord.data = {"devices": devices}
    entry.runtime_data = coord
    added = []
    await async_setup_entry(hass, entry, lambda ents, *_: added.extend(ents))
    return coord, added, [e for e in added if hasattr(e, "_dev_id")]


async def test_heat_pump_temperatures_create_sensors(hass):
    """Nur vorhandene Felder erzeugen Sensoren; Temperaturen sind per Default aktiv."""
    _, _, sensors = await _setup_device_sensors(hass, [_HEATPUMP])
    by_key = {s._key: s for s in sensors}

    for key, value in (("temperatureOutside", 4.5), ("temperatureSupply", 35.0), ("temperatureReturn", 30.5)):
        sensor = by_key[key]
        assert isinstance(sensor, DeviceMeasurementSensor)
        assert sensor.native_value == value
        assert sensor.native_unit_of_measurement == "°C"
        assert sensor.entity_registry_enabled_default is True
    assert "temperatureInside" not in by_key
    assert "currentL1" not in by_key


async def test_device_sensors_are_not_created_twice(hass):
    """Ein weiterer Coordinator-Update legt keine doppelten Entitäten an."""
    coord, added, sensors = await _setup_device_sensors(hass, [_HEATPUMP])
    before = len(added)
    assert len(sensors) == len({s.unique_id for s in sensors})

    coord.async_update_listeners()
    assert len(added) == before

    # Ein neu auftauchendes Feld ergänzt genau einen Sensor
    coord.data["devices"][0] = {**_HEATPUMP, "temperatureInside": 21.0}
    coord.async_update_listeners()
    assert len(added) == before + 1
    assert added[-1]._key == "temperatureInside"


async def test_phase_values_are_disabled_by_default(hass):
    wallbox = {"_id": "wb1", "currentL1": 16.0, "voltageL1": 231.0, "targetPhases": 3, "iWh": 50.0}
    _, _, sensors = await _setup_device_sensors(hass, [wallbox])
    by_key = {s._key: s for s in sensors}

    assert by_key["currentL1"].native_value == 16.0
    assert by_key["currentL1"].native_unit_of_measurement == "A"
    assert by_key["voltageL1"].native_unit_of_measurement == "V"
    assert by_key["targetPhases"].native_value == 3.0
    assert by_key["iWh"].device_class is None
    for key in ("currentL1", "voltageL1", "targetPhases", "iWh"):
        assert by_key[key].entity_registry_enabled_default is False
    assert by_key["currentL1"].unique_id.endswith("_dev_wb1_currentL1")


async def test_measurement_sensor_ignores_invalid_values(hass):
    entry = _entry(hass)
    coord = SolarmanagerCoordinator(hass, entry)
    coord.data = {"devices": [{"_id": "hp1", "temperatureSupply": "n/a"}]}

    sensor = DeviceMeasurementSensor(coord, "hp1", "temperatureSupply", "temperature_supply", None, None, True)

    assert sensor.native_value is None


async def test_updated_at_sensor_parses_timestamp(hass):
    entry = _entry(hass)
    coord = SolarmanagerCoordinator(hass, entry)
    coord.data = {"devices": [dict(_HEATPUMP)]}

    sensor = DeviceUpdatedAtSensor(coord, "hp1")

    value = sensor.native_value
    assert value is not None and value.tzinfo is not None
    assert value.isoformat() == "2026-11-15T20:00:00+00:00"
    assert sensor.entity_registry_enabled_default is False
