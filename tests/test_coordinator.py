"""Tests für Coordinator-Kernlogik: Batterie-Dedup, Merged-PUT-Guard und Auth-Mapping."""
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from freezegun import freeze_time
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solarmanager.const import (
    CLOUD_BASE,
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

HOST = "192.168.1.100"
POINT_URL = f"http://{HOST}/v2/point"
DEVICES_URL = f"http://{HOST}/v2/devices"
LOGIN_URL = f"{CLOUD_BASE}/v1/oauth/login"

SM_ID = "SM1"
STREAM_URL = f"{CLOUD_BASE}/v3/users/{SM_ID}/data/stream"
SENSORS_URL = f"{CLOUD_BASE}/v1/info/sensors/{SM_ID}"
STATS_URL = f"{CLOUD_BASE}/v1/statistics/gateways/{SM_ID}"
LOGIN_RESPONSE = {
    "accessToken": "tok",
    "refreshToken": "ref",
    "tokenType": "Bearer",
    "expiresIn": 3600,
}


def _local_entry(hass) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_MODE: MODE_LOCAL, CONF_HOST: HOST, CONF_SCHEME: "http"},
        unique_id=f"local_{HOST}",
    )
    entry.add_to_hass(hass)
    return entry


def _cloud_entry(hass) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_MODE: MODE_CLOUD,
            CONF_EMAIL: "e@example.com",
            CONF_PASSWORD: "pw",
            CONF_SM_ID: "SM1",
        },
        unique_id=f"{DOMAIN}_SM1",
    )
    entry.add_to_hass(hass)
    return entry


async def test_battery_daily_sum_dedups_same_stream_point(hass, aioclient_mock):
    """Gleicher Stream-Timestamp t → bcWh/bdWh nicht doppelt summieren."""
    entry = _local_entry(hass)
    aioclient_mock.get(DEVICES_URL, json=[])
    aioclient_mock.get(
        POINT_URL,
        json={"t": "2026-07-05T10:00:00Z", "iv": 10, "bcWh": 5.0, "bdWh": 1.0},
    )

    coord = SolarmanagerCoordinator(hass, entry)
    await coord.async_refresh()
    assert coord.last_update_success
    assert coord.data["stat_bat_charge"] == 5.0
    assert coord.data["stat_bat_discharge"] == 1.0

    # Zweiter Poll mit identischem t → Summen unverändert
    await coord.async_refresh()
    assert coord.data["stat_bat_charge"] == 5.0
    assert coord.data["stat_bat_discharge"] == 1.0

    # Neuer Datenpunkt (anderes t) → Summen wachsen
    aioclient_mock.clear_requests()
    aioclient_mock.get(DEVICES_URL, json=[])
    aioclient_mock.get(
        POINT_URL,
        json={"t": "2026-07-05T10:00:10Z", "iv": 10, "bcWh": 5.0, "bdWh": 1.0},
    )
    await coord.async_refresh()
    assert coord.data["stat_bat_charge"] == 10.0
    assert coord.data["stat_bat_discharge"] == 2.0


async def test_battery_daily_sum_skips_points_without_timestamp(hass, aioclient_mock):
    """Punkte ohne Stream-Timestamp t → nicht summieren (kein Dedup möglich)."""
    entry = _local_entry(hass)
    aioclient_mock.get(DEVICES_URL, json=[])
    aioclient_mock.get(POINT_URL, json={"iv": 10, "bcWh": 5.0, "bdWh": 1.0})

    coord = SolarmanagerCoordinator(hass, entry)
    await coord.async_refresh()
    await coord.async_refresh()

    assert coord.last_update_success
    assert coord.data["stat_bat_charge"] == 0.0
    assert coord.data["stat_bat_discharge"] == 0.0


async def test_cloud_grid_import_zero_at_night_despite_rest_stats_consumption(hass, aioclient_mock):
    """iW=0 (Netz unbeteiligt, Batterie deckt Verbrauch) → stat_grid_import bleibt 0,
    auch wenn die REST-Tagesstatistik consumption>0 und selfConsumption=0 zeigt (#25)."""
    entry = _cloud_entry(hass)
    aioclient_mock.post(LOGIN_URL, json=LOGIN_RESPONSE)
    aioclient_mock.get(SENSORS_URL, json=[])
    aioclient_mock.get(STREAM_URL, json={"t": "2026-08-01T02:00:00Z", "iv": 10, "iW": 0, "eW": 0})
    aioclient_mock.get(
        STATS_URL,
        json={
            "production": 0,
            "consumption": 500,
            "selfConsumption": 0,
            "selfConsumptionRate": 0,
            "autarchyDegree": 100,
        },
    )

    coord = SolarmanagerCoordinator(hass, entry)
    await coord.async_refresh()
    assert coord.last_update_success
    assert coord.data["stat_grid_import"] == 0.0
    assert coord.data["stat_grid_export"] == 0.0
    assert coord.data["stat_consumption"] == 500  # REST-Wert bleibt unverändert Quelle

    await coord.async_refresh()
    assert coord.data["stat_grid_import"] == 0.0


async def test_cloud_grid_import_accumulates_over_time(hass, aioclient_mock):
    """iW>0 über ein definiertes Zeitintervall → stat_grid_import wächst um P*dt/3600."""
    entry = _cloud_entry(hass)
    aioclient_mock.post(LOGIN_URL, json=LOGIN_RESPONSE)
    aioclient_mock.get(SENSORS_URL, json=[])
    aioclient_mock.get(STREAM_URL, json={"t": "2026-08-01T10:00:00Z", "iv": 10, "iW": 1200, "eW": 0})
    aioclient_mock.get(
        STATS_URL,
        json={
            "production": 0,
            "consumption": 0,
            "selfConsumption": 0,
            "selfConsumptionRate": None,
            "autarchyDegree": None,
        },
    )

    with freeze_time("2026-08-01 10:00:00+00:00") as frozen:
        coord = SolarmanagerCoordinator(hass, entry)
        await coord.async_refresh()
        assert coord.data["stat_grid_import"] == 0.0  # erster Poll: kein vorheriges _cloud_grid_t

        frozen.tick(delta=timedelta(seconds=30))
        await coord.async_refresh()
        assert coord.data["stat_grid_import"] == pytest.approx(10.0)  # 1200W * 30s/3600
        assert coord.data["stat_grid_export"] == 0.0


async def test_cloud_login_auth_error_maps_to_config_entry_auth_failed(hass, aioclient_mock):
    """401 beim initialen Login → ConfigEntryAuthFailed (löst den Reauth-Flow aus)."""
    entry = _cloud_entry(hass)
    aioclient_mock.post(LOGIN_URL, status=401)

    coord = SolarmanagerCoordinator(hass, entry)
    await coord.async_refresh()
    await hass.async_block_till_done()

    assert not coord.last_update_success
    assert isinstance(coord.last_exception, ConfigEntryAuthFailed)


async def test_put_battery_merged_refuses_without_cached_settings(hass):
    """Ohne gecachte Settings kein PUT — sonst reseten Backend-Defaults andere Felder."""
    entry = _cloud_entry(hass)
    coord = SolarmanagerCoordinator(hass, entry)
    coord.client = AsyncMock()
    coord.device_meta = {}

    with pytest.raises(HomeAssistantError):
        await coord.async_put_battery_merged("dev1", {"batteryMode": 1})

    coord.client.put_battery_settings.assert_not_awaited()


async def test_put_battery_merged_sends_full_settings_object(hass):
    """Merged-PUT überlagert Änderungen über die gecachten Settings."""
    entry = _cloud_entry(hass)
    coord = SolarmanagerCoordinator(hass, entry)
    coord.client = AsyncMock()
    coord.async_refresh_device_meta = AsyncMock()
    coord.device_meta = {
        "dev1": {
            "raw": {
                "data": {
                    "batteryMode": 0,
                    "upperSocLimit": 90,
                    "lowerSocLimit": 10,
                    "unrelatedField": "ignored",
                }
            }
        }
    }

    await coord.async_put_battery_merged("dev1", {"batteryMode": 3})

    coord.client.put_battery_settings.assert_awaited_once()
    _, payload = coord.client.put_battery_settings.await_args.args
    assert payload["batteryMode"] == 3
    assert payload["upperSocLimit"] == 90
    assert payload["lowerSocLimit"] == 10
    # Nur Whitelist-Felder (BATTERY_PUT_FIELDS) werden mitgesendet
    assert "unrelatedField" not in payload


def _point_with_device(ts: str, i_total: float, e_total: float) -> dict:
    return {
        "t": ts,
        "iv": 10,
        "devices": [{"_id": "dev1", "iWhTotal": i_total, "eWhTotal": e_total}],
    }


def _mock_point(aioclient_mock, point: dict) -> None:
    aioclient_mock.clear_requests()
    aioclient_mock.get(DEVICES_URL, json=[])
    aioclient_mock.get(POINT_URL, json=point)


async def test_device_daily_energy_starts_at_zero_and_resets_at_midnight(hass, aioclient_mock):
    """iWhTotal/eWhTotal sind kumulativ → Tageswert ist die Differenz zum Stand um Mitternacht."""
    await hass.config.async_set_time_zone("UTC")
    entry = _local_entry(hass)
    _mock_point(aioclient_mock, _point_with_device("2026-08-01T10:00:00Z", 1000.0, 200.0))

    with freeze_time("2026-08-01 10:00:00+00:00") as frozen:
        coord = SolarmanagerCoordinator(hass, entry)
        await coord.async_refresh()
        assert coord.last_update_success
        dev = coord.data["devices"][0]
        assert dev["iWhToday"] == 0.0
        assert dev["eWhToday"] == 0.0

        # Gleicher Tag: Tageswert wächst mit dem Zählerstand
        _mock_point(aioclient_mock, _point_with_device("2026-08-01T12:00:00Z", 1500.0, 260.0))
        frozen.tick(delta=timedelta(hours=2))
        await coord.async_refresh()
        dev = coord.data["devices"][0]
        assert dev["iWhToday"] == 500.0
        assert dev["eWhToday"] == 60.0

        # Nach Mitternacht: neue Basis, Tageswert startet wieder bei 0
        _mock_point(aioclient_mock, _point_with_device("2026-08-02T00:00:05Z", 1600.0, 300.0))
        frozen.tick(delta=timedelta(hours=12, seconds=5))
        await coord.async_refresh()
        dev = coord.data["devices"][0]
        assert dev["iWhToday"] == 0.0
        assert dev["eWhToday"] == 0.0

        _mock_point(aioclient_mock, _point_with_device("2026-08-02T01:00:00Z", 1750.0, 310.0))
        frozen.tick(delta=timedelta(hours=1))
        await coord.async_refresh()
        dev = coord.data["devices"][0]
        assert dev["iWhToday"] == 150.0
        assert dev["eWhToday"] == 10.0


async def test_device_daily_energy_rebases_on_counter_reset(hass, aioclient_mock):
    """Zählerstand kleiner als die Basis (Reset im Gerät) → neu basieren statt negativ werden."""
    await hass.config.async_set_time_zone("UTC")
    entry = _local_entry(hass)
    _mock_point(aioclient_mock, _point_with_device("2026-08-01T10:00:00Z", 1000.0, 0.0))

    with freeze_time("2026-08-01 10:00:00+00:00") as frozen:
        coord = SolarmanagerCoordinator(hass, entry)
        await coord.async_refresh()

        _mock_point(aioclient_mock, _point_with_device("2026-08-01T11:00:00Z", 40.0, 0.0))
        frozen.tick(delta=timedelta(hours=1))
        await coord.async_refresh()
        assert coord.data["devices"][0]["iWhToday"] == 0.0

        _mock_point(aioclient_mock, _point_with_device("2026-08-01T12:00:00Z", 90.0, 0.0))
        frozen.tick(delta=timedelta(hours=1))
        await coord.async_refresh()
        assert coord.data["devices"][0]["iWhToday"] == 50.0
