"""Geräteliste aus /v3/users/{smId}/devices: Namen, Typen, Entitäten, Prio, Batterie.

Die Fixture ist eine anonymisierte Live-Antwort (14.11.2026). Der Abgleich
gegen /v1/info/sensors hat dort gezeigt: gleiche IDs, gleiche `data`, gleiche
priority. Diese Tests halten fest, dass der Wechsel auf v3 dieselben Entitäten
erzeugt wie vorher v1 — einzig das Fahrzeug (`car`) bekommt keine
Wallbox-Entitäten mehr.
"""
import json
from pathlib import Path
from unittest.mock import AsyncMock

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solarmanager import datetime as sm_datetime
from custom_components.solarmanager import number, select
from custom_components.solarmanager.api_client import SolarmanagerCloud
from custom_components.solarmanager.const import (
    CONF_MODE,
    CONF_SM_ID,
    DOMAIN,
    MODE_CLOUD,
    PLACEHOLDER_DEVICE_NAME,
)
from custom_components.solarmanager.coordinator import (
    BATTERY_PUT_FIELDS,
    SolarmanagerCoordinator,
    _parse_device_items,
)

V3_DEVICES = json.loads(
    (Path(__file__).parent / "fixtures" / "v3_devices.json").read_text(encoding="utf-8")
)
META = _parse_device_items(V3_DEVICES)

BATTERY = "000000000000000000000001"
CAR = "000000000000000000000002"
WALLBOX = "000000000000000000000003"
HEATPUMP = "000000000000000000000004"
INVERTER = "000000000000000000000005"
METER = "000000000000000000000006"
PLUGS = [f"{n:024x}" for n in (7, 8, 9, 10)]


def _entities(meta: dict) -> set[str]:
    """Steuer-Entitäten, die select/number/datetime für ein Gerät anlegen."""
    t = (meta.get("type") or "").lower()
    ents: set[str] = set()
    if t in select.DEVICE_MODE_CONFIG:
        ents.add("select:" + select.DEVICE_MODE_CONFIG[t]["tkey"])
    ents |= {"select:" + c["api_key"] for c in select.DEVICE_EXTRA_SELECTS.get(t, [])}
    if t == "battery":
        ents |= {"number:eco_" + k for k, _ in number.ECO_FIELDS}
    ents |= {"number:" + c["key"] for c in number.DEVICE_NUMBER_CONFIG.get(t, [])}
    if number.is_priority_device(meta):
        ents.add("number:priority")
    ents |= {"datetime:" + c["key"] for c in sm_datetime.DEVICE_DATETIME_CONFIG.get(t, [])}
    return ents


def _cloud_coordinator(hass) -> SolarmanagerCoordinator:
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_MODE: MODE_CLOUD, CONF_SM_ID: "SM1"}, unique_id="SM1"
    )
    entry.add_to_hass(hass)
    coord = SolarmanagerCoordinator(hass, entry)
    coord.cloud = AsyncMock()
    coord.client = coord.cloud
    coord.device_meta = META
    return coord


async def test_cloud_list_devices_uses_v3_endpoint(hass, aioclient_mock):
    aioclient_mock.post("https://cloud.test/v3/auth/refresh", json={"access_token": "t", "expires_in": 3600})
    aioclient_mock.get("https://cloud.test/v3/users/SM1/devices", json=V3_DEVICES)
    client = SolarmanagerCloud(
        async_get_clientsession(hass), base="https://cloud.test", email="", password="", sm_id="SM1", api_key="k"
    )

    assert await client.list_devices() == V3_DEVICES
    assert not any("/v1/info/sensors" in str(call[1]) for call in aioclient_mock.mock_calls)


def test_parse_v3_names():
    assert META[BATTERY]["name"] == "Hausbatterie"  # tag
    assert META[CAR]["name"] == "Testauto"  # carName
    assert [META[p]["name"] for p in PLUGS] == [f"Steckdose {n}" for n in (1, 2, 3, 4)]
    assert not META[BATTERY]["name_is_fallback"]
    assert not META[CAR]["name_is_fallback"]
    # Ohne Tag: Modell als Ersatzname, als solcher markiert
    assert META[INVERTER]["name"] == "Fronius TCP Inv"
    assert META[INVERTER]["name_is_fallback"]
    assert META[METER]["name_is_fallback"]


def test_parse_keeps_v3_type_and_raw():
    assert {m["type"] for m in META.values()} == {
        "battery", "car", "carcharging", "heatpump", "inverter", "smartmeter", "smartplug",
    }
    assert META[BATTERY]["raw"] is next(d for d in V3_DEVICES if d["_id"] == BATTERY)


def test_parse_ignores_garbage():
    assert _parse_device_items(None) == {}
    assert _parse_device_items({"items": []}) == {}
    assert _parse_device_items([{"type": "battery"}, "x"]) == {}


def test_entities_match_previous_v1_result():
    """Entitäten je Gerät — Stand vor der Umstellung (v1), live verglichen."""
    assert len(_entities(META[BATTERY])) == 16
    assert len(_entities(META[WALLBOX])) == 9
    assert _entities(META[HEATPUMP]) == {"select:heat_pump_mode", "number:priority"}
    assert _entities(META[INVERTER]) == {"number:activePowerLimit"}
    assert _entities(META[METER]) == set()
    for plug in PLUGS:
        assert _entities(META[plug]) == {"select:smart_plug_mode", "number:priority"}


def test_vehicle_gets_no_wallbox_entities():
    """`car` ist das Fahrzeug — bis 1.15 bekam es fälschlich Wallbox-Entitäten."""
    assert _entities(META[CAR]) == set()


def test_v3_switches_type_gets_switch_select():
    meta = {"type": "switches", "raw": {"type": "switches", "priority": 3}}
    assert _entities(meta) == {"select:switch_mode", "number:priority"}


def test_priority_devices_match_v1_device_type():
    """v1 device_type == "device" galt für genau diese Geräte."""
    prio = {i for i, m in META.items() if number.is_priority_device(m)}
    assert prio == {BATTERY, WALLBOX, HEATPUMP, *PLUGS}


def test_priority_needs_priority_value():
    assert not number.is_priority_device({"raw": {"type": "smartplug"}})
    # Lokale Liste ohne Cloud-Ergänzung: kein priority → keine Prio-Entität
    assert not number.is_priority_device({"type": "smartplug", "raw": {"_id": "x", "type": "smartplug"}})


async def test_battery_put_sends_all_cached_battery_fields(hass):
    """Read-modify-write mit v3-`data`: kein Batteriefeld darf fehlen."""
    coord = _cloud_coordinator(hass)
    coord.async_refresh_device_meta = AsyncMock()
    data = META[BATTERY]["raw"]["data"]

    await coord.async_put_battery_merged(BATTERY, {"batteryMode": 3})

    _, payload = coord.cloud.put_battery_settings.await_args.args
    assert set(payload) == set(data) & BATTERY_PUT_FIELDS
    assert len(payload) >= 25
    assert payload == {**{k: data[k] for k in payload}, "batteryMode": 3}


async def test_fallback_name_keeps_registered_device_name(hass):
    """Wechselrichter/Smart Meter hiessen unter v1 "Inverter (Fronius TCP Inv)" — bleibt so."""
    coord = _cloud_coordinator(hass)
    registry = dr.async_get(hass)
    registry.async_get_or_create(
        config_entry_id=coord.entry.entry_id,
        identifiers={(DOMAIN, f"device_{INVERTER}")},
        name="Inverter (Fronius TCP Inv)",
    )

    assert coord.get_device_name(INVERTER) == "Inverter (Fronius TCP Inv)"
    # Noch nicht registriert → v3-Ersatzname
    assert coord.get_device_name(METER) == "Fronius TCP SM"


async def test_fallback_name_replaces_placeholder(hass):
    coord = _cloud_coordinator(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=coord.entry.entry_id,
        identifiers={(DOMAIN, f"device_{INVERTER}")},
        name=f"{PLACEHOLDER_DEVICE_NAME} 000005",
    )

    assert coord.get_device_name(INVERTER) == "Fronius TCP Inv"


async def test_tag_rename_still_propagates(hass):
    """Ein im Solar Manager geänderter Tag kommt weiterhin in HA an."""
    coord = _cloud_coordinator(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=coord.entry.entry_id,
        identifiers={(DOMAIN, f"device_{BATTERY}")},
        name="Alter Name",
    )

    assert coord.get_device_name(BATTERY) == "Hausbatterie"
    assert coord.get_device_name("unknown") is None
