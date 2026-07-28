"""Tests für die PV-Überschuss-Condition (Datenverfügbarkeit + `for`-Tracking)."""
from datetime import timedelta

from freezegun import freeze_time
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.condition import ConditionConfig
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solarmanager.condition import SurplusPresentCondition
from custom_components.solarmanager.const import (
    CONF_HOST,
    CONF_MODE,
    CONF_SCHEME,
    DOMAIN,
    MODE_LOCAL,
)
from custom_components.solarmanager.coordinator import SolarmanagerCoordinator

HOST = "192.168.1.100"
FOR = timedelta(minutes=2)


async def _setup(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_MODE: MODE_LOCAL, CONF_HOST: HOST, CONF_SCHEME: "http"},
        unique_id=f"local_{HOST}",
    )
    entry.add_to_hass(hass)
    coord = SolarmanagerCoordinator(hass, entry)
    coord.update_interval = None  # kein Hintergrund-Polling, Tests spulen die Zeit vor
    coord.data = {"pW": 0.0, "cW": 0.0, "batW": 0.0}
    entry.runtime_data = coord
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"site_{coord.site_id}")},
    )
    return coord, device


def _config(
    device_id: str, threshold: float = 0.0, for_duration: timedelta = FOR
) -> ConditionConfig:
    return ConditionConfig(
        target={"device_id": [device_id]},
        options={"threshold": threshold, "for": for_duration},
    )


async def test_unknown_without_power_data(hass):
    coord, device = await _setup(hass)
    coord.data = {"pW": None, "cW": None, "batW": None}
    condition = SurplusPresentCondition(hass, _config(device.id))
    await condition.async_setup()
    try:
        assert condition.async_check() is None
    finally:
        condition.async_unload()


async def test_false_below_threshold(hass):
    coord, device = await _setup(hass)
    coord.data = {"pW": 500.0, "cW": 1000.0, "batW": 0.0}
    condition = SurplusPresentCondition(hass, _config(device.id))
    await condition.async_setup()
    try:
        assert condition.async_check() is False
    finally:
        condition.async_unload()


async def test_false_above_threshold_but_for_duration_not_elapsed(hass):
    coord, device = await _setup(hass)
    with freeze_time("2026-07-28 10:00:00+00:00") as frozen:
        condition = SurplusPresentCondition(hass, _config(device.id))
        await condition.async_setup()
        try:
            coord.async_set_updated_data({"pW": 3000.0, "cW": 1000.0, "batW": 0.0})
            assert condition.async_check() is False

            frozen.tick(delta=timedelta(seconds=30))
            assert condition.async_check() is False
        finally:
            condition.async_unload()


async def test_true_after_for_duration_continuously_above_threshold(hass):
    coord, device = await _setup(hass)
    with freeze_time("2026-07-28 10:00:00+00:00") as frozen:
        condition = SurplusPresentCondition(hass, _config(device.id))
        await condition.async_setup()
        try:
            coord.async_set_updated_data({"pW": 3000.0, "cW": 1000.0, "batW": 0.0})

            frozen.tick(delta=FOR + timedelta(seconds=1))
            assert condition.async_check() is True
        finally:
            condition.async_unload()


async def test_drop_below_threshold_resets_since(hass):
    coord, device = await _setup(hass)
    with freeze_time("2026-07-28 10:00:00+00:00") as frozen:
        condition = SurplusPresentCondition(hass, _config(device.id))
        await condition.async_setup()
        try:
            coord.async_set_updated_data({"pW": 3000.0, "cW": 1000.0, "batW": 0.0})
            frozen.tick(delta=timedelta(seconds=90))
            # Rückfall unter Threshold vor Ablauf der `for`-Dauer -> Uhr wird zurückgesetzt
            coord.async_set_updated_data({"pW": 1000.0, "cW": 1000.0, "batW": 0.0})

            frozen.tick(delta=timedelta(seconds=90))
            assert condition.async_check() is False
        finally:
            condition.async_unload()
