"""Tests für die PV-Überschuss-Condition (Datenverfügbarkeit + `for`-Tracking)."""
from datetime import timedelta

import pytest
from freezegun import freeze_time
from homeassistant.exceptions import HomeAssistantError
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


async def test_validate_config_accepts_real_nested_target_options_shape(hass):
    """Regression: async_validate_complete_config() reicht {target, options} als EIN
    kombiniertes Dict durch, nicht die flachen Options-Felder direkt (führte zu
    "extra keys not allowed @ data['options']" beim Speichern in der echten UI)."""
    validated = await SurplusPresentCondition.async_validate_config(
        hass,
        {
            "target": {"device_id": ["some-device-id"]},
            "options": {"threshold": 500, "for": "00:02:00"},
        },
    )

    assert validated["target"]["device_id"] == ["some-device-id"]
    assert validated["options"]["threshold"] == 500.0
    assert validated["options"]["for"] == FOR


async def test_missing_device_target_raises_but_leaves_object_del_safe(hass):
    """Regression: __init__ warf HomeAssistantError, bevor _remove_listener gesetzt
    war. ConditionChecker.__del__ ruft beim Garbage Collection immer async_unload()
    auf (schluckt Exceptions selbst nur mit einem Log-Eintrag) -- _async_unload griff
    dabei auf das nie gesetzte self._remove_listener zu -> AttributeError im Log.

    target ohne device_id (hier: leer) muss weiterhin sauber HomeAssistantError
    werfen, aber das Objekt muss auch nach der fehlgeschlagenen Konstruktion
    _async_unload()-sicher sein.
    """
    config = ConditionConfig(target={}, options={"threshold": 0.0, "for": FOR})
    instance = SurplusPresentCondition.__new__(SurplusPresentCondition)

    with pytest.raises(HomeAssistantError):
        instance.__init__(hass, config)

    assert instance._remove_listener is None
    instance._async_unload()  # darf nicht mit AttributeError crashen


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
