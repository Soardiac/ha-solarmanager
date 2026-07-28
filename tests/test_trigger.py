"""Tests für den PV-Überschuss-Trigger (Flanke + `for`-Debounce)."""
from datetime import timedelta

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.trigger import TriggerConfig
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.solarmanager.const import (
    CONF_HOST,
    CONF_MODE,
    CONF_SCHEME,
    DOMAIN,
    MODE_LOCAL,
)
from custom_components.solarmanager.coordinator import SolarmanagerCoordinator
from custom_components.solarmanager.trigger import SurplusAvailableTrigger

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


def _config(device_id: str, threshold: float = 0.0, for_duration: timedelta = FOR) -> TriggerConfig:
    return TriggerConfig(
        key="solarmanager.surplus_available",
        target={"device_id": [device_id]},
        options={"threshold": threshold, "for": for_duration},
    )


async def test_fires_after_for_duration_above_threshold(hass):
    coord, device = await _setup(hass)
    trigger = SurplusAvailableTrigger(hass, _config(device.id))
    fired = []
    unsub = await trigger.async_attach_runner(
        lambda payload, description, context=None: fired.append(payload)
    )
    try:
        coord.async_set_updated_data({"pW": 3000.0, "cW": 1000.0, "batW": 0.0})
        assert fired == []  # Flanke erkannt, `for`-Dauer aber noch nicht abgelaufen

        async_fire_time_changed(hass, dt_util.utcnow() + FOR + timedelta(seconds=1))
        await hass.async_block_till_done()

        assert len(fired) == 1
        assert fired[0]["surplus_w"] == 2000.0
    finally:
        unsub()


async def test_drop_below_threshold_before_for_duration_cancels(hass):
    coord, device = await _setup(hass)
    trigger = SurplusAvailableTrigger(hass, _config(device.id))
    fired = []
    unsub = await trigger.async_attach_runner(
        lambda payload, description, context=None: fired.append(payload)
    )
    try:
        coord.async_set_updated_data({"pW": 3000.0, "cW": 1000.0, "batW": 0.0})

        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
        await hass.async_block_till_done()
        coord.async_set_updated_data({"pW": 1000.0, "cW": 1000.0, "batW": 0.0})

        async_fire_time_changed(hass, dt_util.utcnow() + FOR + timedelta(seconds=1))
        await hass.async_block_till_done()

        assert fired == []
    finally:
        unsub()


async def test_missing_data_cancels_pending_phase(hass):
    coord, device = await _setup(hass)
    trigger = SurplusAvailableTrigger(hass, _config(device.id))
    fired = []
    unsub = await trigger.async_attach_runner(
        lambda payload, description, context=None: fired.append(payload)
    )
    try:
        coord.async_set_updated_data({"pW": 3000.0, "cW": 1000.0, "batW": 0.0})
        coord.async_set_updated_data({"pW": None, "cW": 1000.0, "batW": 0.0})

        async_fire_time_changed(hass, dt_util.utcnow() + FOR + timedelta(seconds=1))
        await hass.async_block_till_done()

        assert fired == []
    finally:
        unsub()


async def test_stays_above_threshold_only_fires_once(hass):
    coord, device = await _setup(hass)
    trigger = SurplusAvailableTrigger(hass, _config(device.id))
    fired = []
    unsub = await trigger.async_attach_runner(
        lambda payload, description, context=None: fired.append(payload)
    )
    try:
        coord.async_set_updated_data({"pW": 3000.0, "cW": 1000.0, "batW": 0.0})
        async_fire_time_changed(hass, dt_util.utcnow() + FOR + timedelta(seconds=1))
        await hass.async_block_till_done()
        assert len(fired) == 1

        # Bleibt über Threshold, aber keine neue Flanke -> kein Re-Trigger
        coord.async_set_updated_data({"pW": 3100.0, "cW": 1000.0, "batW": 0.0})
        async_fire_time_changed(hass, dt_util.utcnow() + FOR + timedelta(seconds=1))
        await hass.async_block_till_done()
        assert len(fired) == 1
    finally:
        unsub()


async def test_attach_runner_raises_for_unknown_device(hass):
    await _setup(hass)
    trigger = SurplusAvailableTrigger(hass, _config("nonexistent-device-id"))

    with pytest.raises(HomeAssistantError):
        await trigger.async_attach_runner(lambda *a, **k: None)


async def test_validate_config_accepts_real_nested_target_options_shape(hass):
    """Regression: async_validate_complete_config() reicht {target, options} als EIN
    kombiniertes Dict durch, nicht die flachen Options-Felder direkt (führte zu
    "extra keys not allowed @ data['options']" beim Speichern in der echten UI)."""
    validated = await SurplusAvailableTrigger.async_validate_config(
        hass,
        {
            "target": {"device_id": ["some-device-id"]},
            "options": {"threshold": 500, "for": "00:02:00"},
        },
    )

    assert validated["target"]["device_id"] == ["some-device-id"]
    assert validated["options"]["threshold"] == 500.0
    assert validated["options"]["for"] == timedelta(minutes=2)


async def test_validate_config_fills_option_defaults_when_omitted(hass):
    validated = await SurplusAvailableTrigger.async_validate_config(
        hass, {"target": {"device_id": ["some-device-id"]}}
    )

    assert validated["options"]["threshold"] == 0.0
    assert validated["options"]["for"] == timedelta(minutes=2)
