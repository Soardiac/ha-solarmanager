"""Tests für den lokalen Modus mit Cloud-Ergänzung.

Lesen bleibt lokal, Schreiben und reine Cloud-Daten (Geräte-Einstellungen,
Prognose, kWp) laufen über die Cloud. Ohne Option: keinerlei Cloud-Verkehr.
"""
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from freezegun import freeze_time
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solarmanager.api_client import SolarmanagerApiError
from custom_components.solarmanager.const import (
    CLOUD_BASE,
    CONF_API_KEY,
    CONF_CLOUD_API_KEY,
    CONF_CLOUD_EXTENSION,
    CONF_HOST,
    CONF_MODE,
    CONF_SCHEME,
    CONF_SM_ID,
    DOMAIN,
    MODE_LOCAL,
)
from custom_components.solarmanager.coordinator import (
    SolarmanagerCoordinator,
    cloud_extension_enabled,
    cloud_extension_issue_id,
)

HOST = "192.168.1.100"
SM_ID = "SM1"
POINT_URL = f"http://{HOST}/v2/point"
DEVICES_URL = f"http://{HOST}/v2/devices"
AUTH_URL = f"{CLOUD_BASE}/v3/auth/refresh"
DEVICES_V3_URL = f"{CLOUD_BASE}/v3/users/{SM_ID}/devices"
FORECAST_URL = f"{CLOUD_BASE}/v3/users/{SM_ID}/data/forecast"
GATEWAY_URL = f"{CLOUD_BASE}/v1/info/gateway/{SM_ID}"

POINT = {"t": "2026-08-01T10:00:00Z", "iv": 10, "pW": 3000, "cW": 800}
LOCAL_DEVICES = [{"deviceId": "plug1", "description": "Entfeuchter", "type": "smartplug"}]
CLOUD_SENSORS = [
    {"_id": "plug1", "type": "smartplug", "tag": "Cloud-Name", "priority": 1, "data": {"smartPlugChargingType": 5}}
]
FORECAST = {"data": [{"t": "2026-08-01T10:15:00.000Z", "pW": 2500, "pWmin": 2000, "pWmax": 3000}]}


def _entry(hass, *, extension: bool = True, with_credentials: bool = True) -> MockConfigEntry:
    data = {CONF_MODE: MODE_LOCAL, CONF_HOST: HOST, CONF_SCHEME: "http", CONF_API_KEY: None}
    if with_credentials:
        data |= {CONF_SM_ID: SM_ID, CONF_CLOUD_API_KEY: "cloud-key"}
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=data,
        options={CONF_CLOUD_EXTENSION: extension},
        unique_id=f"local_{HOST}",
    )
    entry.add_to_hass(hass)
    return entry


def _mock_local(aioclient_mock) -> None:
    aioclient_mock.get(POINT_URL, json=POINT)
    aioclient_mock.get(DEVICES_URL, json=LOCAL_DEVICES)


def _mock_cloud(aioclient_mock, *, auth_status: int = 200, sensors_status: int = 200) -> None:
    aioclient_mock.post(
        AUTH_URL,
        status=auth_status,
        json={"access_token": "tok", "expires_in": 86400},
    )
    aioclient_mock.get(DEVICES_V3_URL, status=sensors_status, json=CLOUD_SENSORS)
    aioclient_mock.get(FORECAST_URL, json=FORECAST)
    aioclient_mock.get(GATEWAY_URL, json={"settings": {"kWp": 11.2}})


def _cloud_calls(aioclient_mock) -> list[str]:
    return [str(call[1]) for call in aioclient_mock.mock_calls if str(call[1]).startswith(CLOUD_BASE)]


async def test_extension_requires_option_and_credentials(hass):
    assert cloud_extension_enabled(_entry(hass))
    assert not cloud_extension_enabled(
        MockConfigEntry(domain=DOMAIN, data={CONF_MODE: MODE_LOCAL}, options={CONF_CLOUD_EXTENSION: True})
    )
    assert not cloud_extension_enabled(
        MockConfigEntry(
            domain=DOMAIN,
            data={CONF_MODE: MODE_LOCAL, CONF_SM_ID: SM_ID, CONF_CLOUD_API_KEY: "k"},
            options={CONF_CLOUD_EXTENSION: False},
        )
    )


async def test_no_cloud_traffic_without_extension(hass, aioclient_mock):
    """Harte Zusage: Ohne Cloud-Ergänzung verlässt keine Anfrage das lokale Netz."""
    entry = _entry(hass, extension=False)
    _mock_local(aioclient_mock)

    with freeze_time("2026-08-01 10:00:00+00:00") as frozen:
        coord = SolarmanagerCoordinator(hass, entry)
        for _ in range(5):
            await coord.async_refresh()
            frozen.tick(timedelta(hours=1))

    assert coord.last_update_success
    assert not coord.has_cloud
    assert not coord.can_write
    assert coord.cloud is None
    assert _cloud_calls(aioclient_mock) == []


async def test_hybrid_reads_locally_and_loads_cloud_only_data(hass, aioclient_mock):
    entry = _entry(hass)
    _mock_local(aioclient_mock)
    _mock_cloud(aioclient_mock)

    with freeze_time("2026-08-01 10:05:00+00:00"):
        coord = SolarmanagerCoordinator(hass, entry)
        await coord.async_refresh()

    assert coord.last_update_success
    assert coord.has_cloud and coord.can_write
    assert coord.data["pW"] == 3000.0  # aus /v2/point
    assert coord.forecast is not None
    assert coord.api_kwp == 11.2
    # Meta: Name und Typ lokal, Einstellungen (data) aus der Cloud
    meta = coord.device_meta["plug1"]
    assert meta["name"] == "Entfeuchter"
    assert meta["type"] == "smartplug"
    assert meta["raw"]["data"] == {"smartPlugChargingType": 5}
    # Der Stream kommt nie aus der Cloud
    assert not any("data/stream" in url for url in _cloud_calls(aioclient_mock))


async def test_hybrid_keeps_last_cloud_settings_when_cloud_fails(hass, aioclient_mock):
    entry = _entry(hass)
    _mock_local(aioclient_mock)
    _mock_cloud(aioclient_mock)
    coord = SolarmanagerCoordinator(hass, entry)
    await coord.async_refresh()

    aioclient_mock.clear_requests()
    _mock_local(aioclient_mock)
    _mock_cloud(aioclient_mock, sensors_status=500)
    await coord._load_device_meta()

    assert coord.device_meta["plug1"]["raw"]["data"] == {"smartPlugChargingType": 5}


async def test_hybrid_cloud_auth_error_keeps_local_running_and_raises_issue(hass, aioclient_mock):
    entry = _entry(hass)
    _mock_local(aioclient_mock)
    _mock_cloud(aioclient_mock, auth_status=401)

    coord = SolarmanagerCoordinator(hass, entry)
    await coord.async_refresh()

    assert coord.last_update_success
    assert coord.data["pW"] == 3000.0
    issue_id = cloud_extension_issue_id(entry)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    # Cloud wieder ok → Meldung verschwindet beim nächsten erfolgreichen Aufruf
    aioclient_mock.clear_requests()
    _mock_local(aioclient_mock)
    _mock_cloud(aioclient_mock)
    await coord._load_device_meta()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_cloud_put_uses_cloud_client_and_reloads_settings(hass):
    entry = _entry(hass)
    coord = SolarmanagerCoordinator(hass, entry)
    coord.cloud = AsyncMock()
    coord.async_refresh_device_meta = AsyncMock()

    await coord.async_cloud_put("put_smart_plug_mode", "plug1", {"smartPlugChargingType": 1})

    coord.cloud.put_smart_plug_mode.assert_awaited_once_with("plug1", {"smartPlugChargingType": 1})
    coord.async_refresh_device_meta.assert_awaited_once()


async def test_cloud_put_errors_become_home_assistant_errors(hass):
    coord = SolarmanagerCoordinator(hass, _entry(hass, extension=False))
    with pytest.raises(HomeAssistantError) as err:
        await coord.async_cloud_put("put_smart_plug_mode", "plug1", {})
    assert err.value.translation_key == "cloud_not_enabled"

    coord.cloud = AsyncMock()
    coord.cloud.put_smart_plug_mode.side_effect = SolarmanagerApiError("boom")
    with pytest.raises(HomeAssistantError) as err:
        await coord.async_cloud_put("put_smart_plug_mode", "plug1", {})
    assert err.value.translation_key == "cloud_write_failed"


@pytest.mark.parametrize("extension", [True, False])
async def test_control_and_forecast_entities_only_with_extension(hass, extension):
    from custom_components.solarmanager import select, sensor

    entry = _entry(hass, extension=extension)
    coord = SolarmanagerCoordinator(hass, entry)
    coord.data = {}
    coord.device_meta = {"plug1": {"name": "Entfeuchter", "type": "smartplug", "raw": {}}}
    entry.runtime_data = coord

    selects: list = []
    await select.async_setup_entry(hass, entry, lambda e, update=False: selects.extend(e))
    sensors: list = []
    await sensor.async_setup_entry(hass, entry, lambda e, update=False: sensors.extend(e))

    assert bool(selects) is extension
    has_forecast = any(s.unique_id.endswith("_forecast_power_now") for s in sensors)
    assert has_forecast is extension
