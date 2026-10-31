"""Tests für Einspeiselimit-/Abregelungs-Trigger und -Conditions sowie den Generatorleistungs-Sensor (#23)."""
from datetime import datetime, timedelta, timezone

import pytest
import voluptuous as vol
from freezegun import freeze_time
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.condition import ConditionConfig
from homeassistant.helpers.trigger import TriggerConfig
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.solarmanager.condition import (
    CurtailmentExpectedTodayCondition,
    GeneratorSharePowerCondition,
)
from custom_components.solarmanager.const import (
    CONF_EMAIL,
    CONF_GENERATOR_KWP,
    CONF_MODE,
    CONF_PASSWORD,
    CONF_SM_ID,
    DOMAIN,
    MODE_CLOUD,
)
from custom_components.solarmanager.coordinator import SolarmanagerCoordinator
from custom_components.solarmanager.forecast import parse_forecast
from custom_components.solarmanager.sensor import GeneratorKwpSensor
from custom_components.solarmanager.trigger import GeneratorShareReachedTrigger

UTC = timezone.utc
FOR = timedelta(minutes=2)


async def _setup(hass, kwp: float | None = 10.0):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_MODE: MODE_CLOUD, CONF_EMAIL: "e@example.com", CONF_PASSWORD: "pw", CONF_SM_ID: "SM1"},
        options={CONF_GENERATOR_KWP: kwp} if kwp else {},
        unique_id=f"{DOMAIN}_SM1",
    )
    entry.add_to_hass(hass)
    coord = SolarmanagerCoordinator(hass, entry)
    coord.update_interval = None  # kein Hintergrund-Polling
    coord.data = {"pW": 0.0, "eW": 0.0}
    entry.runtime_data = coord
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"site_{coord.site_id}")},
    )
    return coord, device


async def _setup_second(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_MODE: MODE_CLOUD, CONF_EMAIL: "e@example.com", CONF_PASSWORD: "pw", CONF_SM_ID: "SM2"},
        options={CONF_GENERATOR_KWP: 10.0},
        unique_id=f"{DOMAIN}_SM2",
    )
    entry.add_to_hass(hass)
    coord = SolarmanagerCoordinator(hass, entry)
    coord.update_interval = None
    entry.runtime_data = coord
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"site_{coord.site_id}")},
    )
    return coord, device


def _share_config(device_id: str, source: str = "feed_in", percent: float = 60.0) -> ConditionConfig:
    return ConditionConfig(
        target={"device_id": [device_id]},
        options={"source": source, "percent": percent, "for": FOR},
    )


def _expected_config(device_id: str, band: str = "expected", percent: float = 60.0) -> ConditionConfig:
    return ConditionConfig(target={"device_id": [device_id]}, options={"percent": percent, "band": band})


# -------------------- Leistung über Anteil der Generatorleistung --------------------

async def test_share_true_after_for_when_feed_in_reaches_limit(hass):
    coord, device = await _setup(hass)  # 10 kWp → 60 % = 6000 W
    with freeze_time("2026-08-01 10:00:00+00:00") as frozen:
        condition = GeneratorSharePowerCondition(hass, _share_config(device.id))
        await condition.async_setup()
        try:
            assert condition.async_check() is False

            coord.async_set_updated_data({"pW": 7000.0, "eW": 6000.0})
            assert condition.async_check() is False  # `for` noch nicht abgelaufen

            frozen.tick(FOR)
            assert condition.async_check() is True

            # Verbraucher eingeschaltet: Einspeisung sinkt → feed_in wird falsch
            coord.async_set_updated_data({"pW": 7200.0, "eW": 4000.0})
            assert condition.async_check() is False
        finally:
            condition.async_unload()


async def test_share_with_production_stays_true_after_consumer_switched_on(hass):
    coord, device = await _setup(hass)
    with freeze_time("2026-08-01 10:00:00+00:00") as frozen:
        condition = GeneratorSharePowerCondition(hass, _share_config(device.id, source="production"))
        await condition.async_setup()
        try:
            coord.async_set_updated_data({"pW": 6500.0, "eW": 6000.0})
            frozen.tick(FOR)
            assert condition.async_check() is True

            coord.async_set_updated_data({"pW": 7200.0, "eW": 4000.0})
            assert condition.async_check() is True
        finally:
            condition.async_unload()


async def test_share_unknown_without_generator_kwp_or_power(hass):
    coord, device = await _setup(hass, kwp=None)
    coord.data = {"pW": 9000.0, "eW": 9000.0}
    condition = GeneratorSharePowerCondition(hass, _share_config(device.id))
    await condition.async_setup()
    try:
        assert condition.async_check() is None
    finally:
        condition.async_unload()

    coord2, device2 = await _setup_second(hass)
    coord2.data = {"pW": None, "eW": None}
    condition = GeneratorSharePowerCondition(hass, _share_config(device2.id))
    await condition.async_setup()
    try:
        assert condition.async_check() is None
    finally:
        condition.async_unload()


async def test_share_validate_config_fills_defaults(hass):
    validated = await GeneratorSharePowerCondition.async_validate_config(
        hass, {"target": {"device_id": ["dev"]}, "options": {}}
    )
    assert validated["options"] == {"source": "feed_in", "percent": 60.0, "for": FOR}

    with pytest.raises(vol.Invalid):
        await GeneratorSharePowerCondition.async_validate_config(
            hass, {"target": {"device_id": ["dev"]}, "options": {"percent": 150}}
        )


# -------------------- Heute Abregelung zu erwarten --------------------

def _forecast(peak_watts: float, peak_max: float, end_utc: datetime) -> object:
    """Eine Viertelstunde mit der Spitze, endend bei end_utc (t = Intervallende)."""
    return parse_forecast(
        {"data": [{"t": end_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "pW": peak_watts, "pWmin": 0, "pWmax": peak_max}]}
    )


@pytest.mark.parametrize(
    ("band", "expected"),
    [("expected", False), ("max", True)],
)
async def test_expected_today_compares_band_with_limit(hass, band, expected):
    coord, device = await _setup(hass)  # 6000 W bei 60 %
    coord.forecast = _forecast(5000, 7000, datetime(2026, 8, 1, 11, 15, tzinfo=UTC))
    with freeze_time("2026-08-01 08:00:00+00:00"):
        condition = CurtailmentExpectedTodayCondition(hass, _expected_config(device.id, band=band))
        await condition.async_setup()
        assert condition.async_check() is expected


async def test_expected_today_false_once_peak_has_passed(hass):
    coord, device = await _setup(hass)
    coord.forecast = _forecast(8000, 9000, datetime(2026, 8, 1, 11, 15, tzinfo=UTC))
    condition = CurtailmentExpectedTodayCondition(hass, _expected_config(device.id))
    await condition.async_setup()
    with freeze_time("2026-08-01 08:00:00+00:00"):
        assert condition.async_check() is True
    with freeze_time("2026-08-01 12:00:00+00:00"):
        assert condition.async_check() is False


async def test_expected_today_ignores_tomorrow(hass):
    coord, device = await _setup(hass)
    coord.forecast = _forecast(8000, 9000, datetime(2026, 8, 2, 11, 15, tzinfo=UTC))
    condition = CurtailmentExpectedTodayCondition(hass, _expected_config(device.id))
    await condition.async_setup()
    with freeze_time("2026-08-01 08:00:00+00:00"):
        assert condition.async_check() is False


async def test_expected_today_unknown_without_forecast_or_kwp(hass):
    coord, device = await _setup(hass)
    condition = CurtailmentExpectedTodayCondition(hass, _expected_config(device.id))
    await condition.async_setup()
    assert condition.async_check() is None  # keine Prognose

    coord2, device2 = await _setup_second(hass)
    hass.config_entries.async_update_entry(coord2.entry, options={})
    coord2.forecast = _forecast(8000, 9000, datetime(2026, 8, 1, 11, 15, tzinfo=UTC))
    condition = CurtailmentExpectedTodayCondition(hass, _expected_config(device2.id))
    await condition.async_setup()
    assert condition.async_check() is None  # keine Generatorleistung


async def test_expected_today_validate_config_fills_defaults(hass):
    validated = await CurtailmentExpectedTodayCondition.async_validate_config(
        hass, {"target": {"device_id": ["dev"]}, "options": {}}
    )
    assert validated["options"] == {"percent": 60.0, "band": "expected"}


# -------------------- Diagnose-Sensor --------------------

async def test_generator_kwp_sensor_shows_value_and_source(hass):
    coord, _ = await _setup(hass, kwp=11.2)
    sensor = GeneratorKwpSensor(coord)

    assert sensor.native_value == 11.2
    assert sensor.extra_state_attributes == {"source": "option"}
    assert sensor.entity_category == "diagnostic"

    hass.config_entries.async_update_entry(coord.entry, options={})
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {"source": None}


# -------------------- Trigger: Anteil der Generatorleistung erreicht --------------------

def _trigger_config(device_id: str, source: str = "feed_in", percent: float = 60.0) -> TriggerConfig:
    return TriggerConfig(
        key="solarmanager.generator_share_reached",
        target={"device_id": [device_id]},
        options={"source": source, "percent": percent, "for": FOR},
    )


async def _attach(hass, trigger):
    fired: list[dict] = []
    unsub = await trigger.async_attach_runner(
        lambda payload, description, context=None: fired.append(payload)
    )
    return fired, unsub


async def _advance(hass, delta: timedelta) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + delta)
    await hass.async_block_till_done()


async def test_trigger_fires_once_after_for_when_limit_reached(hass):
    coord, device = await _setup(hass)  # 10 kWp → 6000 W
    fired, unsub = await _attach(hass, GeneratorShareReachedTrigger(hass, _trigger_config(device.id)))
    try:
        coord.async_set_updated_data({"pW": 7000.0, "eW": 6000.0})
        assert fired == []

        await _advance(hass, FOR + timedelta(seconds=1))
        assert fired == [
            {"source": "feed_in", "power_w": 6000.0, "threshold_w": 6000.0, "percent": 60.0}
        ]

        # Dauerhaft über der Schwelle → kein erneutes Auslösen
        coord.async_set_updated_data({"pW": 7100.0, "eW": 6100.0})
        await _advance(hass, 2 * FOR)
        assert len(fired) == 1
    finally:
        unsub()


async def test_trigger_rearms_after_dropping_below(hass):
    coord, device = await _setup(hass)
    fired, unsub = await _attach(hass, GeneratorShareReachedTrigger(hass, _trigger_config(device.id)))
    try:
        coord.async_set_updated_data({"pW": 7000.0, "eW": 6500.0})
        await _advance(hass, FOR + timedelta(seconds=1))
        coord.async_set_updated_data({"pW": 7000.0, "eW": 3000.0})
        coord.async_set_updated_data({"pW": 7000.0, "eW": 6500.0})
        await _advance(hass, 2 * FOR + timedelta(seconds=2))
        assert len(fired) == 2
    finally:
        unsub()


async def test_trigger_cancelled_when_dropping_below_before_for(hass):
    coord, device = await _setup(hass)
    fired, unsub = await _attach(hass, GeneratorShareReachedTrigger(hass, _trigger_config(device.id)))
    try:
        coord.async_set_updated_data({"pW": 7000.0, "eW": 6500.0})
        coord.async_set_updated_data({"pW": 7000.0, "eW": 5000.0})
        await _advance(hass, FOR + timedelta(seconds=1))
        assert fired == []
    finally:
        unsub()


async def test_trigger_uses_production_when_selected(hass):
    coord, device = await _setup(hass)
    fired, unsub = await _attach(
        hass, GeneratorShareReachedTrigger(hass, _trigger_config(device.id, source="production"))
    )
    try:
        coord.async_set_updated_data({"pW": 6200.0, "eW": 1000.0})
        await _advance(hass, FOR + timedelta(seconds=1))
        assert len(fired) == 1
        assert fired[0]["power_w"] == 6200.0
    finally:
        unsub()


async def test_trigger_never_fires_without_generator_kwp(hass):
    coord, device = await _setup(hass, kwp=None)
    fired, unsub = await _attach(hass, GeneratorShareReachedTrigger(hass, _trigger_config(device.id)))
    try:
        coord.async_set_updated_data({"pW": 20000.0, "eW": 20000.0})
        await _advance(hass, FOR + timedelta(seconds=1))
        assert fired == []
    finally:
        unsub()


async def test_trigger_validate_config_fills_defaults(hass):
    validated = await GeneratorShareReachedTrigger.async_validate_config(
        hass, {"target": {"device_id": ["dev"]}, "options": {}}
    )
    assert validated["options"] == {"source": "feed_in", "percent": 60.0, "for": FOR}
