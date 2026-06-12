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
    CONF_HOST,
    CONF_MODE,
    CONF_PASSWORD,
    CONF_SM_ID,
    DOMAIN,
    MODE_CLOUD,
    MODE_LOCAL,
)

_PATCH_CLOUD = "custom_components.solarmanager.config_flow.SolarmanagerCloud"
_PATCH_LOCAL = "custom_components.solarmanager.config_flow.SolarmanagerLocal"

_CLOUD_INPUT = {
    CONF_EMAIL: "test@example.com",
    CONF_PASSWORD: "secret",
    CONF_SM_ID: "SM-0001",
    CONF_API_KEY: "",
}


async def _start_user_flow(hass):
    """Hilfsfunktion: Flow starten und Modus wählen, gibt flow_id zurück."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    return result["flow_id"]


async def test_user_flow_mode_selector(hass):
    """Erster Schritt zeigt Modus-Auswahl."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert CONF_MODE in result["data_schema"].schema


async def test_cloud_flow_success(hass):
    """Cloud Happy-Path: Modus wählen → Zugangsdaten → Entry angelegt."""
    flow_id = await _start_user_flow(hass)

    # Schritt 1: Modus wählen → weiter zu cloud
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_MODE: MODE_CLOUD}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "cloud"

    # Schritt 2: Zugangsdaten
    with patch(_PATCH_CLOUD) as mock_cls:
        mock_cls.return_value.login = AsyncMock()
        mock_cls.return_value.stream_user_v3 = AsyncMock(return_value={})

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _CLOUD_INPUT
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Solarmanager SM-0001"
    assert result["data"][CONF_EMAIL] == "test@example.com"
    assert result["data"][CONF_SM_ID] == "SM-0001"
    assert result["data"][CONF_MODE] == MODE_CLOUD


async def test_cloud_flow_auth_error(hass):
    """Falsches Passwort → Formular bleibt mit Fehler 'auth'."""
    flow_id = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_MODE: MODE_CLOUD}
    )

    with patch(_PATCH_CLOUD) as mock_cls:
        mock_cls.return_value.login = AsyncMock(side_effect=SolarmanagerAuthError)

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _CLOUD_INPUT
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "auth"


async def test_cloud_flow_cannot_connect(hass):
    """API nicht erreichbar → Formular bleibt mit Fehler 'cannot_connect'."""
    flow_id = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_MODE: MODE_CLOUD}
    )

    with patch(_PATCH_CLOUD) as mock_cls:
        mock_cls.return_value.login = AsyncMock(side_effect=SolarmanagerApiError)

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _CLOUD_INPUT
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "cannot_connect"


async def test_local_flow_success(hass):
    """Lokaler Happy-Path: Modus lokal → IP → Entry angelegt."""
    flow_id = await _start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_MODE: MODE_LOCAL}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "local"

    with patch(_PATCH_LOCAL) as mock_cls:
        mock_cls.return_value.get_point = AsyncMock(return_value={})

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.1.100"}
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Solarmanager Local (192.168.1.100)"
    assert result["data"][CONF_MODE] == MODE_LOCAL
    assert result["data"][CONF_HOST] == "192.168.1.100"


async def test_local_flow_https_host(hass):
    """HTTPS-Eingabe: Scheme bleibt im gespeicherten Host, Titel ist scheme-los."""
    flow_id = await _start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_MODE: MODE_LOCAL}
    )
    assert result["step_id"] == "local"

    with patch(_PATCH_LOCAL) as mock_cls:
        mock_cls.return_value.get_point = AsyncMock(return_value={})

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "https://192.168.1.100"}
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Solarmanager Local (192.168.1.100)"
    assert result["data"][CONF_HOST] == "https://192.168.1.100"


async def test_local_flow_duplicate_scheme(hass):
    """Gleiches Gateway mit anderem Scheme → bereits konfiguriert, Abbruch."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_MODE: MODE_LOCAL, CONF_HOST: "192.168.1.100"},
        unique_id="local_192.168.1.100",
    )
    entry.add_to_hass(hass)

    flow_id = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_MODE: MODE_LOCAL}
    )

    with patch(_PATCH_LOCAL) as mock_cls:
        mock_cls.return_value.get_point = AsyncMock(return_value={})

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "https://192.168.1.100"}
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_local_flow_cannot_connect(hass):
    """Lokales Gateway nicht erreichbar → Fehler 'cannot_connect'."""
    flow_id = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_MODE: MODE_LOCAL}
    )

    with patch(_PATCH_LOCAL) as mock_cls:
        mock_cls.return_value.get_point = AsyncMock(side_effect=SolarmanagerApiError)

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.1.100"}
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "cannot_connect"


async def test_reauth_flow_success(hass):
    """Re-Auth (Cloud): neue Zugangsdaten werden gespeichert."""
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


async def test_reauth_aborted_for_local_mode(hass):
    """Re-Auth für lokalen Eintrag wird sofort abgebrochen."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_MODE: MODE_LOCAL, CONF_HOST: "192.168.1.100"},
        unique_id="local_192.168.1.100",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_not_supported"
