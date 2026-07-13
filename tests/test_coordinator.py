"""Tests für Coordinator-Kernlogik: Batterie-Dedup, Merged-PUT-Guard und Auth-Mapping."""
from unittest.mock import AsyncMock

import pytest

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
