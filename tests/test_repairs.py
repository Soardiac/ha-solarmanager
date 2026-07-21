"""Tests für das Repair-Issue der E-Mail/Passwort-Migration und den Fix-Flow."""
from unittest.mock import MagicMock, patch

from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solarmanager import (
    _password_auth_issue_id,
    _review_password_auth_issue,
)
from custom_components.solarmanager.const import (
    CONF_API_KEY,
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
from custom_components.solarmanager.repairs import (
    DeprecatedPasswordAuthRepairFlow,
    async_create_fix_flow,
)


def _cloud_entry(hass, *, api_key: str | None) -> MockConfigEntry:
    data = {
        CONF_MODE: MODE_CLOUD,
        CONF_EMAIL: "a@b.ch",
        CONF_PASSWORD: "secret",
        CONF_SM_ID: "sm123",
    }
    if api_key:
        data[CONF_API_KEY] = api_key
    entry = MockConfigEntry(domain=DOMAIN, data=data, unique_id=f"{DOMAIN}_sm123")
    entry.add_to_hass(hass)
    return entry


def _local_entry(hass) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_MODE: MODE_LOCAL, CONF_HOST: "192.168.1.100", CONF_SCHEME: "http"},
        unique_id="local_192.168.1.100",
    )
    entry.add_to_hass(hass)
    return entry


def _coord(is_local: bool) -> MagicMock:
    coord = MagicMock()
    coord.is_local = is_local
    return coord


async def test_issue_created_for_cloud_without_api_key(hass):
    """Cloud-Entry ohne API-Key → Migrationswarnung erscheint."""
    entry = _cloud_entry(hass, api_key=None)
    _review_password_auth_issue(hass, entry, _coord(is_local=False))

    issue = ir.async_get(hass).async_get_issue(DOMAIN, _password_auth_issue_id(entry))
    assert issue is not None
    assert issue.is_fixable is True
    assert issue.translation_placeholders == {"deadline": "30.06.2027", "sm_id": "sm123"}


async def test_no_issue_for_cloud_with_api_key(hass):
    """Cloud-Entry mit API-Key → keine Warnung."""
    entry = _cloud_entry(hass, api_key="key-xyz")
    _review_password_auth_issue(hass, entry, _coord(is_local=False))

    assert ir.async_get(hass).async_get_issue(DOMAIN, _password_auth_issue_id(entry)) is None


async def test_existing_issue_deleted_after_migration(hass):
    """Wird nachträglich ein API-Key gesetzt, verschwindet ein bestehendes Issue."""
    entry = _cloud_entry(hass, api_key=None)
    _review_password_auth_issue(hass, entry, _coord(is_local=False))
    assert ir.async_get(hass).async_get_issue(DOMAIN, _password_auth_issue_id(entry)) is not None

    hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_API_KEY: "key-xyz"})
    _review_password_auth_issue(hass, entry, _coord(is_local=False))
    assert ir.async_get(hass).async_get_issue(DOMAIN, _password_auth_issue_id(entry)) is None


async def test_no_issue_for_local_entry(hass):
    """Local-Modus → nie eine Migrationswarnung."""
    entry = _local_entry(hass)
    _review_password_auth_issue(hass, entry, _coord(is_local=True))

    assert ir.async_get(hass).async_get_issue(DOMAIN, _password_auth_issue_id(entry)) is None


async def test_fix_flow_triggers_reauth(hass):
    """Bestätigung im Fix-Flow stößt den bestehenden Reauth-Flow an."""
    entry = _cloud_entry(hass, api_key=None)

    flow = await async_create_fix_flow(
        hass, _password_auth_issue_id(entry), {"entry_id": entry.entry_id}
    )
    assert isinstance(flow, DeprecatedPasswordAuthRepairFlow)
    flow.hass = hass

    with patch.object(
        type(entry), "async_start_reauth", autospec=True
    ) as mock_reauth:
        result = await flow.async_step_confirm(user_input={})

    assert mock_reauth.call_count == 1
    assert result["type"] == "create_entry"


async def test_fix_flow_shows_form_first(hass):
    """Ohne Eingabe zeigt der Fix-Flow zuerst das Bestätigungsformular."""
    entry = _cloud_entry(hass, api_key=None)
    _review_password_auth_issue(hass, entry, _coord(is_local=False))

    flow = await async_create_fix_flow(
        hass, _password_auth_issue_id(entry), {"entry_id": entry.entry_id}
    )
    flow.hass = hass
    flow.issue_id = _password_auth_issue_id(entry)

    result = await flow.async_step_confirm(user_input=None)
    assert result["type"] == "form"
    assert result["step_id"] == "confirm"
    assert result["description_placeholders"] == {"deadline": "30.06.2027", "sm_id": "sm123"}
