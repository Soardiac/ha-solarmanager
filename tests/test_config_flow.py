"""Tests für den Config Flow (Bronze-Anforderung: Setup + Re-Auth)."""
from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solarmanager.api_client import (
    SolarmanagerApiError,
    SolarmanagerAuthError,
)
from custom_components.solarmanager.const import (
    CONF_API_KEY,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_SM_ID,
    DOMAIN,
)

_PATCH_CLOUD = "custom_components.solarmanager.config_flow.SolarmanagerCloud"

_USER_INPUT = {
    CONF_EMAIL: "test@example.com",
    CONF_PASSWORD: "secret",
    CONF_SM_ID: "SM-0001",
    CONF_API_KEY: "",
}


async def test_user_flow_success(hass):
    """Happy-Path: Entry wird korrekt angelegt."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    with patch(_PATCH_CLOUD) as mock_cls:
        mock_cls.return_value.login = AsyncMock()
        mock_cls.return_value.stream_user_v3 = AsyncMock(return_value={})

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Solarmanager SM-0001"
    assert result["data"][CONF_EMAIL] == "test@example.com"
    assert result["data"][CONF_SM_ID] == "SM-0001"


async def test_user_flow_auth_error(hass):
    """Falsches Passwort → Formular bleibt mit Fehler 'auth'."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(_PATCH_CLOUD) as mock_cls:
        mock_cls.return_value.login = AsyncMock(side_effect=SolarmanagerAuthError)

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "auth"


async def test_user_flow_cannot_connect(hass):
    """API nicht erreichbar → Formular bleibt mit Fehler 'cannot_connect'."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(_PATCH_CLOUD) as mock_cls:
        mock_cls.return_value.login = AsyncMock(side_effect=SolarmanagerApiError)

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "cannot_connect"


async def test_reauth_flow_success(hass):
    """Re-Auth: neue Zugangsdaten werden gespeichert, Entry wird neu geladen."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_EMAIL: "old@example.com",
            CONF_PASSWORD: "oldpass",
            CONF_SM_ID: "SM-0001",
            CONF_API_KEY: None,
        },
        unique_id=f"{DOMAIN}_SM-0001",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    with patch(_PATCH_CLOUD) as mock_cls, patch.object(
        hass.config_entries, "async_reload"
    ):
        mock_cls.return_value.login = AsyncMock()

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_EMAIL: "new@example.com", CONF_PASSWORD: "newpass", CONF_API_KEY: ""},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_EMAIL] == "new@example.com"
    assert entry.data[CONF_PASSWORD] == "newpass"


async def test_reauth_flow_auth_error(hass):
    """Re-Auth mit falschem Passwort → Formular bleibt mit Fehler 'auth'."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_EMAIL: "test@example.com",
            CONF_PASSWORD: "oldpass",
            CONF_SM_ID: "SM-0001",
            CONF_API_KEY: None,
        },
        unique_id=f"{DOMAIN}_SM-0001",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )

    with patch(_PATCH_CLOUD) as mock_cls:
        mock_cls.return_value.login = AsyncMock(side_effect=SolarmanagerAuthError)

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_EMAIL: "test@example.com", CONF_PASSWORD: "wrong", CONF_API_KEY: ""},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "auth"
