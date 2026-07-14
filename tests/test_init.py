"""Tests für __init__: Geräte-Entfernung über die Device-Registry."""
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solarmanager import async_remove_config_entry_device
from custom_components.solarmanager.const import (
    CONF_HOST,
    CONF_MODE,
    CONF_SCHEME,
    DOMAIN,
    MODE_LOCAL,
)


def _entry(hass) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_MODE: MODE_LOCAL, CONF_HOST: "192.168.1.100", CONF_SCHEME: "http"},
        unique_id="local_192.168.1.100",
    )
    entry.add_to_hass(hass)
    return entry


async def test_remove_device_allowed_when_entry_not_loaded(hass):
    """Entry ohne runtime_data (nie geladen) → Entfernen erlaubt statt AttributeError."""
    entry = _entry(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "device_abc123")},
    )

    assert await async_remove_config_entry_device(hass, entry, device) is True


async def test_remove_site_device_refused_when_entry_not_loaded(hass):
    """Das Site-Gerät bleibt auch ohne runtime_data geschützt."""
    entry = _entry(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "site_xyz")},
    )

    assert await async_remove_config_entry_device(hass, entry, device) is False
