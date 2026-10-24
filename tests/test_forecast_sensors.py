"""Tests für die Prognose-Sensoren und die Energie-Dashboard-Plattform."""
from datetime import datetime, timedelta, timezone

import pytest
from freezegun import freeze_time
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solarmanager.const import (
    CONF_EMAIL,
    CONF_HOST,
    CONF_MODE,
    CONF_PASSWORD,
    CONF_SCHEME,
    CONF_SM_ID,
    DOMAIN,
    MODE_CLOUD,
    MODE_LOCAL,
)
from custom_components.solarmanager.coordinator import SolarmanagerCoordinator
from custom_components.solarmanager.energy import async_get_solar_forecast
from custom_components.solarmanager.forecast import parse_forecast
from custom_components.solarmanager.sensor import (
    ForecastEnergySensor,
    ForecastPeakPowerSensor,
    ForecastPeakTimeSensor,
    ForecastPowerSensor,
)

UTC = timezone.utc
# 01.08.2026 in Zürich = UTC+2 → lokaler Tag 31.07. 22:00 UTC bis 01.08. 22:00 UTC
NOW = "2026-08-01 10:05:00+00:00"


def _series(start: datetime, watts: list[float]) -> dict:
    return {
        "data": [
            {
                "t": (start + timedelta(minutes=15 * i)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "pW": w,
                "pWmin": w / 2,
                "pWmax": w * 2,
            }
            for i, w in enumerate(watts)
        ]
    }


@pytest.fixture
async def zurich(hass):
    await hass.config.async_set_time_zone("Europe/Zurich")


def _cloud_coord(hass) -> SolarmanagerCoordinator:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_MODE: MODE_CLOUD, CONF_EMAIL: "e@example.com", CONF_PASSWORD: "pw", CONF_SM_ID: "SM1"},
        unique_id=f"{DOMAIN}_SM1",
    )
    entry.add_to_hass(hass)
    coord = SolarmanagerCoordinator(hass, entry)
    coord.data = {}
    entry.runtime_data = coord
    return coord


def _with_forecast(coord: SolarmanagerCoordinator) -> SolarmanagerCoordinator:
    # Heute: 10:00–10:30 UTC mit 1000/2000 W, kurz vor Mitternacht lokal 400 W;
    # morgen: 10:00 UTC mit 4000 W
    today = _series(datetime(2026, 8, 1, 10, tzinfo=UTC), [1000, 2000])["data"]
    late = _series(datetime(2026, 8, 1, 21, 45, tzinfo=UTC), [400])["data"]
    tomorrow = _series(datetime(2026, 8, 2, 10, tzinfo=UTC), [4000])["data"]
    coord.forecast = parse_forecast({"data": today + late + tomorrow})
    return coord


async def test_power_sensors_now_and_ahead(hass, zurich):
    coord = _with_forecast(_cloud_coord(hass))
    now = ForecastPowerSensor(coord, "forecast_power_now", "forecast_power_now", 0, with_series=True)
    in15 = ForecastPowerSensor(coord, "forecast_power_15m", "forecast_power_15m", 15)
    in30 = ForecastPowerSensor(coord, "forecast_power_30m", "forecast_power_30m", 30)

    with freeze_time(NOW):
        assert now.available
        assert now.native_value == 1000
        assert in15.native_value == 2000
        assert in30.native_value is None  # 10:35 liegt in der Lücke bis 21:45
        attrs = now.extra_state_attributes
        assert attrs["min"] == 500
        assert attrs["max"] == 2000
        assert [p["w"] for p in attrs["forecast"]] == [1000, 2000, 400, 4000]
        assert attrs["forecast"][0]["t"] == "2026-08-01T12:00:00+02:00"
    assert "forecast" in now._unrecorded_attributes


async def test_energy_sensors_today_remaining_tomorrow(hass, zurich):
    coord = _with_forecast(_cloud_coord(hass))
    today = ForecastEnergySensor(coord, "forecast_energy_today", "forecast_energy_today", "today")
    remaining = ForecastEnergySensor(coord, "forecast_energy_remaining_today", "x", "remaining_today")
    tomorrow = ForecastEnergySensor(coord, "forecast_energy_tomorrow", "x", "tomorrow")

    with freeze_time(NOW):
        # 1000*0.25 + 2000*0.25 + 400*0.25
        assert today.native_value == 850
        # ab 10:05: 1000 W * 10 min + 500 + 100
        assert remaining.native_value == round(1000 * 10 / 60 + 600)
        assert tomorrow.native_value == 1000
        attrs = today.extra_state_attributes
        assert attrs["min"] == 425
        assert attrs["max"] == 1700
        # Zeitreihe beginnt erst um 10:00 UTC → heutiger Tag nicht vollständig abgedeckt
        assert attrs["complete"] is False


async def test_peak_sensors(hass, zurich):
    coord = _with_forecast(_cloud_coord(hass))
    power = ForecastPeakPowerSensor(coord)
    when = ForecastPeakTimeSensor(coord)

    with freeze_time(NOW):
        assert power.native_value == 2000
        assert power.extra_state_attributes == {
            "time": "2026-08-01T12:15:00+02:00",
            "min": 1000,
            "max": 4000,
        }
        assert when.native_value == datetime(2026, 8, 1, 10, 15, tzinfo=UTC)


async def test_peak_sensors_unknown_without_production(hass, zurich):
    coord = _cloud_coord(hass)
    coord.forecast = parse_forecast(_series(datetime(2026, 8, 1, 10, tzinfo=UTC), [0, 0]))

    with freeze_time(NOW):
        assert ForecastPeakPowerSensor(coord).native_value is None
        assert ForecastPeakTimeSensor(coord).native_value is None


async def test_sensors_unavailable_without_forecast(hass, zurich):
    coord = _cloud_coord(hass)
    sensor = ForecastPowerSensor(coord, "forecast_power_now", "forecast_power_now", 0)

    assert not sensor.available
    assert sensor.native_value is None
    assert sensor.extra_state_attributes is None


async def test_energy_platform_returns_hourly_forecast(hass, zurich):
    coord = _with_forecast(_cloud_coord(hass))

    result = await async_get_solar_forecast(hass, coord.entry.entry_id)

    assert result == {
        "wh_hours": {
            "2026-08-01T10:00:00+00:00": 750,
            "2026-08-01T21:00:00+00:00": 100,
            "2026-08-02T10:00:00+00:00": 1000,
        }
    }


async def test_energy_platform_none_without_forecast_local_or_unknown_entry(hass):
    coord = _cloud_coord(hass)
    assert await async_get_solar_forecast(hass, coord.entry.entry_id) is None
    assert await async_get_solar_forecast(hass, "does-not-exist") is None

    local = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_MODE: MODE_LOCAL, CONF_HOST: "192.168.1.100", CONF_SCHEME: "http"},
        unique_id="local_192.168.1.100",
    )
    local.add_to_hass(hass)
    local.runtime_data = SolarmanagerCoordinator(hass, local)
    assert await async_get_solar_forecast(hass, local.entry_id) is None


@pytest.mark.parametrize(("local", "expected"), [(False, True), (True, False)])
async def test_forecast_sensors_only_created_in_cloud_mode(hass, local, expected):
    from custom_components.solarmanager.sensor import async_setup_entry

    if local:
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_MODE: MODE_LOCAL, CONF_HOST: "192.168.1.100", CONF_SCHEME: "http"},
            unique_id="local_192.168.1.100",
        )
        entry.add_to_hass(hass)
        coord = SolarmanagerCoordinator(hass, entry)
        coord.data = {}
        entry.runtime_data = coord
    else:
        coord = _cloud_coord(hass)
        entry = coord.entry

    added: list = []
    await async_setup_entry(hass, entry, lambda entities, update=False: added.extend(entities))
    keys = {e.unique_id.removeprefix(f"{entry.entry_id}_") for e in added}

    forecast_keys = {
        "forecast_power_now",
        "forecast_power_15m",
        "forecast_power_30m",
        "forecast_energy_today",
        "forecast_energy_remaining_today",
        "forecast_energy_tomorrow",
        "forecast_peak_power_today",
        "forecast_peak_time_today",
    }
    assert forecast_keys <= keys if expected else not (forecast_keys & keys)
