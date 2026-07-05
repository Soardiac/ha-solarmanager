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
    CONF_SCHEME,
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

_LOCAL_INPUT = {
    CONF_HOST: "192.168.1.100",
    CONF_SCHEME: "http",
    CONF_API_KEY: "",
}


async def _start_user_flow(hass):
    """Flow starten und zurückgeben — bleibt beim Modus-Selector."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    return result["flow_id"]


# ---------------------------------------------------------------------------
# Modus-Selector
# ---------------------------------------------------------------------------

async def test_user_flow_mode_selector(hass):
    """Erster Schritt zeigt Modus-Auswahl."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert CONF_MODE in result["data_schema"].schema


# ---------------------------------------------------------------------------
# Cloud-Flow
# ---------------------------------------------------------------------------

async def test_cloud_flow_success(hass):
    """Cloud Happy-Path: Modus → Zugangsdaten → Entry angelegt."""
    flow_id = await _start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_MODE: MODE_CLOUD}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "cloud"

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
    """API nicht erreichbar → Fehler 'cannot_connect'."""
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


# ---------------------------------------------------------------------------
# Lokaler Flow
# ---------------------------------------------------------------------------

async def test_local_flow_success(hass):
    """Lokaler Happy-Path: IP + http → Entry angelegt."""
    flow_id = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_MODE: MODE_LOCAL}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "local"

    with patch(_PATCH_LOCAL) as mock_cls:
        mock_cls.return_value.get_point = AsyncMock(return_value={})

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _LOCAL_INPUT
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Solarmanager Local (192.168.1.100)"
    assert result["data"][CONF_MODE] == MODE_LOCAL
    assert result["data"][CONF_HOST] == "192.168.1.100"
    assert result["data"][CONF_SCHEME] == "http"
    assert result["data"][CONF_API_KEY] is None


async def test_local_flow_https(hass):
    """HTTPS-Modus: Scheme separat gespeichert, Host bleibt IP-only."""
    flow_id = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_MODE: MODE_LOCAL}
    )

    with patch(_PATCH_LOCAL) as mock_cls:
        mock_cls.return_value.get_point = AsyncMock(return_value={})

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: "192.168.1.100", CONF_SCHEME: "https", CONF_API_KEY: ""},
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HOST] == "192.168.1.100"
    assert result["data"][CONF_SCHEME] == "https"


async def test_local_flow_with_api_key(hass):
    """Lokaler Flow mit API Key → wird gespeichert."""
    flow_id = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_MODE: MODE_LOCAL}
    )

    with patch(_PATCH_LOCAL) as mock_cls:
        mock_cls.return_value.get_point = AsyncMock(return_value={})

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: "192.168.1.100", CONF_SCHEME: "http", CONF_API_KEY: "secret123"},
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_API_KEY] == "secret123"


async def test_local_flow_duplicate(hass):
    """Gleiches Gateway bereits konfiguriert → already_configured."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_MODE: MODE_LOCAL, CONF_HOST: "192.168.1.100", CONF_SCHEME: "http"},
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
            result["flow_id"],
            {CONF_HOST: "192.168.1.100", CONF_SCHEME: "https", CONF_API_KEY: ""},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_local_flow_cannot_connect(hass):
    """Gateway nicht erreichbar → Fehler 'cannot_connect'."""
    flow_id = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_MODE: MODE_LOCAL}
    )

    with patch(_PATCH_LOCAL) as mock_cls:
        mock_cls.return_value.get_point = AsyncMock(side_effect=SolarmanagerApiError)

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _LOCAL_INPUT
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "cannot_connect"


# ---------------------------------------------------------------------------
# Reauth
# ---------------------------------------------------------------------------

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


async def test_reauth_api_key_only_keeps_credentials(hass):
    """Re-Auth nur mit API Key → gespeicherte E-Mail/Passwort bleiben erhalten."""
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

    with patch(_PATCH_CLOUD) as mock_cls, patch.object(
        hass.config_entries, "async_reload"
    ):
        mock_cls.return_value.login = AsyncMock()

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_EMAIL: "", CONF_PASSWORD: "", CONF_API_KEY: "new-api-key"},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_EMAIL] == "old@example.com"
    assert entry.data[CONF_PASSWORD] == "oldpass"
    assert entry.data[CONF_API_KEY] == "new-api-key"


async def test_reauth_aborted_for_local_mode(hass):
    """Re-Auth für lokalen Eintrag wird sofort abgebrochen."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_MODE: MODE_LOCAL, CONF_HOST: "192.168.1.100", CONF_SCHEME: "http"},
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


# ---------------------------------------------------------------------------
# Reconfigure
# ---------------------------------------------------------------------------

async def test_reconfigure_cloud_success(hass):
    """Reconfigure (Cloud): sm_id wird geaendert, unique_id/title folgen."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_MODE: MODE_CLOUD,
            CONF_EMAIL: "test@example.com",
            CONF_PASSWORD: "secret",
            CONF_SM_ID: "SM-0001",
            CONF_API_KEY: None,
        },
        unique_id=f"{DOMAIN}_SM-0001",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
        data=entry.data,
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure_cloud"

    with patch(_PATCH_CLOUD) as mock_cls, patch.object(
        hass.config_entries, "async_reload"
    ):
        mock_cls.return_value.login = AsyncMock()
        mock_cls.return_value.stream_user_v3 = AsyncMock(return_value={})

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_EMAIL: "", CONF_PASSWORD: "", CONF_SM_ID: "SM-0002", CONF_API_KEY: ""},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_SM_ID] == "SM-0002"
    assert entry.data[CONF_EMAIL] == "test@example.com"  # leer -> behalten
    assert entry.unique_id == f"{DOMAIN}_SM-0002"


async def test_reconfigure_cloud_duplicate_sm_id_aborts(hass):
    """Reconfigure auf eine sm_id, die ein anderer Eintrag nutzt -> Abbruch."""
    other = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_MODE: MODE_CLOUD, CONF_SM_ID: "SM-0002"},
        unique_id=f"{DOMAIN}_SM-0002",
    )
    other.add_to_hass(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_MODE: MODE_CLOUD,
            CONF_EMAIL: "test@example.com",
            CONF_PASSWORD: "secret",
            CONF_SM_ID: "SM-0001",
        },
        unique_id=f"{DOMAIN}_SM-0001",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
        data=entry.data,
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: "", CONF_PASSWORD: "", CONF_SM_ID: "SM-0002", CONF_API_KEY: ""},
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reconfigure_local_success(hass):
    """Reconfigure (Lokal): Host-Wechsel aktualisiert Daten und unique_id."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_MODE: MODE_LOCAL, CONF_HOST: "192.168.1.100", CONF_SCHEME: "http"},
        unique_id="local_192.168.1.100",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
        data=entry.data,
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure_local"

    with patch(_PATCH_LOCAL) as mock_cls, patch.object(
        hass.config_entries, "async_reload"
    ):
        mock_cls.return_value.get_point = AsyncMock(return_value={})

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: "192.168.1.50", CONF_SCHEME: "https", CONF_API_KEY: ""},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == "192.168.1.50"
    assert entry.data[CONF_SCHEME] == "https"
    assert entry.unique_id == "local_192.168.1.50"
