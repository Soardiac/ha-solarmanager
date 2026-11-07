"""Tests für __init__: Geräte-Entfernung, Registrierung und Migration des Site-Geräts."""
import logging
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solarmanager import (
    async_migrate_entry,
    async_remove_config_entry_device,
    async_setup_entry,
)
from custom_components.solarmanager.const import (
    CONF_HOST,
    CONF_MODE,
    CONF_SCHEME,
    CONF_SM_ID,
    DOMAIN,
    MODE_CLOUD,
    MODE_LOCAL,
)

HOST = "192.168.1.100"
NEW_HOST = "192.168.1.200"
SM_ID = "SM-0001"


def _entry(hass, *, minor_version: int = 2) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_MODE: MODE_LOCAL, CONF_HOST: HOST, CONF_SCHEME: "http"},
        unique_id=f"local_{HOST}",
        version=1,
        minor_version=minor_version,
    )
    entry.add_to_hass(hass)
    return entry


def _site_identifier(entry) -> set[tuple[str, str]]:
    return {(DOMAIN, f"site_{entry.entry_id}")}


def _site_device(hass, entry, site_id: str, *, entities: int = 0) -> dr.DeviceEntry:
    """Site-Gerät mit altem Identifier site_{host|smId}, optional mit Entitäten."""
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"site_{site_id}")},
        name="Solar Manager",
    )
    ent_reg = er.async_get(hass)
    for i in range(entities):
        ent_reg.async_get_or_create(
            "sensor",
            DOMAIN,
            f"{entry.entry_id}_{site_id}_{i}",
            config_entry=entry,
            device_id=device.id,
        )
    return device


def _mock_local_api(aioclient_mock, host: str = HOST) -> None:
    aioclient_mock.get(f"http://{host}/v2/point", json={"iv": 10, "pW": 0, "cW": 0})
    aioclient_mock.get(f"http://{host}/v2/devices", json=[])


async def test_remove_device_allowed_when_entry_not_loaded(hass):
    """Entry ohne runtime_data (nie geladen) → Entfernen erlaubt statt AttributeError."""
    entry = _entry(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "device_abc123")},
    )

    assert await async_remove_config_entry_device(hass, entry, device) is True


async def test_remove_site_device_refused(hass):
    """Das Site-Gerät bleibt geschützt, auch ohne runtime_data."""
    entry = _entry(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers=_site_identifier(entry),
    )

    assert await async_remove_config_entry_device(hass, entry, device) is False


async def test_remove_stale_site_device_allowed(hass):
    """Ein Site-Gerät mit altem Identifier (site_{host|smId}) ist ein Überbleibsel
    und darf entfernt werden."""
    entry = _entry(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"site_{SM_ID}")},
    )

    assert await async_remove_config_entry_device(hass, entry, device) is True


async def test_setup_stores_site_device_id(hass, aioclient_mock):
    """Die Registry-ID des Site-Geräts landet am Coordinator.

    child_device_info() verknüpft die Kind-Geräte über via_device_id mit der
    Site. Fehlt die ID, verlieren alle Kind-Geräte stillschweigend ihre
    Zuordnung (Issue #30).
    """
    entry = _entry(hass)
    # async_config_entry_first_refresh() besteht auf diesem Zustand
    entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)
    _mock_local_api(aioclient_mock)

    with patch.object(
        hass.config_entries, "async_forward_entry_setups", AsyncMock()
    ):
        assert await async_setup_entry(hass, entry) is True

    # Über die Registry-ID auflösen statt über die Identifier: async_get_device()
    # ist seit HA 2026.9 deprecated (Identifier sind nicht mehr eindeutig über
    # Config-Entries hinweg) und bricht in 2027.8.
    site_device = dr.async_get(hass).async_get(entry.runtime_data.site_device_id)
    assert site_device is not None
    assert site_device.identifiers == _site_identifier(entry)


async def test_setup_renames_existing_site_device_but_keeps_user_name(hass, aioclient_mock):
    """Bestehende Installationen: „Solarmanager <id>“ wird zu „Solar Manager“,
    ein vom Nutzer vergebener Name bleibt erhalten."""
    entry = _entry(hass)
    registry = dr.async_get(hass)
    old = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers=_site_identifier(entry),
        name=f"Solarmanager {HOST}",
    )
    registry.async_update_device(old.id, name_by_user="Mein PV")
    entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)
    _mock_local_api(aioclient_mock)

    with patch.object(
        hass.config_entries, "async_forward_entry_setups", AsyncMock()
    ):
        assert await async_setup_entry(hass, entry) is True

    device = registry.async_get(old.id)
    assert device.name == "Solar Manager"
    assert device.name_by_user == "Mein PV"
    assert device.serial_number == HOST


async def test_setup_after_ip_change_keeps_site_device(hass, aioclient_mock):
    """Neue Gateway-IP (Reconfigure) → dasselbe Site-Gerät, nur die
    Seriennummer ändert sich (Discussion #26)."""
    entry = _entry(hass)
    entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)
    _mock_local_api(aioclient_mock)
    _mock_local_api(aioclient_mock, NEW_HOST)

    with patch.object(
        hass.config_entries, "async_forward_entry_setups", AsyncMock()
    ):
        assert await async_setup_entry(hass, entry) is True
        first_id = entry.runtime_data.site_device_id

        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_HOST: NEW_HOST}
        )
        assert await async_setup_entry(hass, entry) is True

    assert entry.runtime_data.site_device_id == first_id
    registry = dr.async_get(hass)
    assert registry.async_get(first_id).serial_number == NEW_HOST
    site_devices = [
        d
        for d in dr.async_entries_for_config_entry(registry, entry.entry_id)
        if any(ident.startswith("site_") for _, ident in d.identifiers)
    ]
    assert len(site_devices) == 1


async def test_migrate_moves_identifier_onto_existing_device(hass):
    """Bestehendes Site-Gerät bekommt den neuen Identifier; device_id, Nutzername
    und Bereich bleiben erhalten."""
    entry = _entry(hass, minor_version=1)
    device = _site_device(hass, entry, HOST, entities=2)
    area = ar.async_get(hass).async_create("Technikraum")
    registry = dr.async_get(hass)
    registry.async_update_device(device.id, name_by_user="Mein PV", area_id=area.id)

    assert await async_migrate_entry(hass, entry) is True

    migrated = registry.async_get(device.id)
    assert migrated.identifiers == _site_identifier(entry)
    assert migrated.name_by_user == "Mein PV"
    assert migrated.area_id == area.id
    assert entry.minor_version == 3


async def test_migrate_removes_orphans_from_mode_switch(hass):
    """Nach früheren Wechseln Cloud <-> Lokal bleibt genau ein Site-Gerät: das
    des aktuellen Modus. Karteileichen ohne Entitäten werden entfernt."""
    entry = _entry(hass, minor_version=1)
    orphan = _site_device(hass, entry, SM_ID)
    current = _site_device(hass, entry, HOST, entities=3)

    assert await async_migrate_entry(hass, entry) is True

    registry = dr.async_get(hass)
    assert registry.async_get(orphan.id) is None
    assert registry.async_get(current.id).identifiers == _site_identifier(entry)


async def test_migrate_without_current_device_picks_one_with_entities(hass):
    """IP geändert, aber noch kein Setup mit der neuen IP gelaufen: Das Gerät mit
    den Entitäten gewinnt, nicht die leere Karteileiche."""
    entry = _entry(hass, minor_version=1)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_HOST: NEW_HOST}
    )
    with_entities = _site_device(hass, entry, HOST, entities=1)
    orphan = _site_device(hass, entry, SM_ID)

    assert await async_migrate_entry(hass, entry) is True

    registry = dr.async_get(hass)
    assert registry.async_get(with_entities.id).identifiers == _site_identifier(entry)
    assert registry.async_get(orphan.id) is None


async def test_migrate_keeps_stale_device_with_entities(hass, caplog):
    """Ein veraltetes Site-Gerät mit Entitäten wird nicht gelöscht, nur gemeldet."""
    entry = _entry(hass, minor_version=1)
    current = _site_device(hass, entry, HOST, entities=2)
    stale = _site_device(hass, entry, SM_ID, entities=1)

    with caplog.at_level(logging.WARNING):
        assert await async_migrate_entry(hass, entry) is True

    registry = dr.async_get(hass)
    assert registry.async_get(current.id).identifiers == _site_identifier(entry)
    assert registry.async_get(stale.id) is not None
    assert "Veraltetes Site-Gerät" in caplog.text


async def test_migrate_leaves_child_devices_alone(hass):
    """Kind-Geräte (device_…) sind keine Site-Geräte und bleiben unangetastet."""
    entry = _entry(hass, minor_version=1)
    site = _site_device(hass, entry, HOST, entities=1)
    child = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "device_abc123")},
        via_device_id=site.id,
    )

    assert await async_migrate_entry(hass, entry) is True

    child_after = dr.async_get(hass).async_get(child.id)
    assert child_after.identifiers == {(DOMAIN, "device_abc123")}
    assert child_after.via_device_id == site.id


async def test_migrate_without_site_device_only_bumps_version(hass):
    entry = _entry(hass, minor_version=1)

    assert await async_migrate_entry(hass, entry) is True
    assert entry.minor_version == 3


async def test_migrate_is_idempotent(hass):
    """Läuft die Migration erneut, ändert sich nichts."""
    entry = _entry(hass, minor_version=1)
    device = _site_device(hass, entry, HOST, entities=1)

    assert await async_migrate_entry(hass, entry) is True
    hass.config_entries.async_update_entry(entry, minor_version=1)
    assert await async_migrate_entry(hass, entry) is True

    assert dr.async_get(hass).async_get(device.id).identifiers == _site_identifier(entry)


@pytest.mark.parametrize(
    ("data", "old_title"),
    [
        ({CONF_MODE: MODE_CLOUD, CONF_SM_ID: SM_ID}, f"Solarmanager {SM_ID}"),
        (
            {CONF_MODE: MODE_LOCAL, CONF_HOST: HOST, CONF_SCHEME: "http"},
            f"Solarmanager Local ({HOST})",
        ),
    ],
)
async def test_migrate_replaces_generated_title(hass, data, old_title):
    """Automatisch erzeugte Titel werden zu „Solar Manager“ (unabhängig vom Modus)."""
    entry = _entry(hass, minor_version=2)
    hass.config_entries.async_update_entry(entry, data=data, title=old_title)

    assert await async_migrate_entry(hass, entry) is True

    assert entry.title == "Solar Manager"
    assert entry.minor_version == 3


@pytest.mark.parametrize(
    "custom_title", ["Mein PV", "Solarmanager Garage", "Solarmanager", "Solarmanager SM-9999"]
)
async def test_migrate_keeps_custom_title(hass, custom_title):
    """Selbst vergebene Titel bleiben unangetastet."""
    entry = _entry(hass, minor_version=2)
    hass.config_entries.async_update_entry(entry, title=custom_title)

    assert await async_migrate_entry(hass, entry) is True

    assert entry.title == custom_title
    assert entry.minor_version == 3


async def test_migrate_refuses_future_major_version(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={}, version=2)
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry) is False
