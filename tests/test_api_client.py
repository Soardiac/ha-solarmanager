"""Tests für den Cloud-API-Client: Fehler-Mapping und 401-Retry."""
import aiohttp
import pytest

from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.solarmanager.api_client import (
    SolarmanagerApiError,
    SolarmanagerAuthError,
    SolarmanagerCloud,
    SolarmanagerRateLimit,
)

BASE = "https://cloud.test"
LOGIN_URL = f"{BASE}/v1/oauth/login"
REFRESH_URL = f"{BASE}/v1/oauth/refresh"
STREAM_URL = f"{BASE}/v3/users/SM1/data/stream"

_TOKEN_RESPONSE = {
    "accessToken": "tok",
    "refreshToken": "ref",
    "tokenType": "Bearer",
    "expiresIn": 3600,
}


def _client(hass) -> SolarmanagerCloud:
    return SolarmanagerCloud(
        async_get_clientsession(hass),
        base=BASE,
        email="e@example.com",
        password="pw",
        sm_id="SM1",
        api_key=None,
    )


async def test_login_and_stream_success(hass, aioclient_mock):
    """Happy path: Login liefert Token, Stream liefert Daten."""
    aioclient_mock.post(LOGIN_URL, json=_TOKEN_RESPONSE)
    aioclient_mock.get(STREAM_URL, json={"pW": 1234})

    client = _client(hass)
    await client.login()
    data = await client.stream_user_v3()

    assert data == {"pW": 1234}


async def test_network_error_maps_to_api_error(hass, aioclient_mock):
    """aiohttp.ClientError beim Login → SolarmanagerApiError (nicht 'unknown')."""
    aioclient_mock.post(LOGIN_URL, exc=aiohttp.ClientError("connection refused"))

    with pytest.raises(SolarmanagerApiError):
        await _client(hass).login()


async def test_stream_network_error_maps_to_api_error(hass, aioclient_mock):
    """Netzwerkfehler bei authentifizierten Requests → SolarmanagerApiError."""
    aioclient_mock.post(LOGIN_URL, json=_TOKEN_RESPONSE)
    aioclient_mock.get(STREAM_URL, exc=aiohttp.ClientError("timeout"))

    client = _client(hass)
    await client.login()
    with pytest.raises(SolarmanagerApiError):
        await client.stream_user_v3()


async def test_login_401_raises_auth_error(hass, aioclient_mock):
    """401 beim Login → SolarmanagerAuthError."""
    aioclient_mock.post(LOGIN_URL, status=401)

    with pytest.raises(SolarmanagerAuthError):
        await _client(hass).login()


async def test_stream_429_raises_rate_limit(hass, aioclient_mock):
    """429 → SolarmanagerRateLimit."""
    aioclient_mock.post(LOGIN_URL, json=_TOKEN_RESPONSE)
    aioclient_mock.get(STREAM_URL, status=429)

    client = _client(hass)
    await client.login()
    with pytest.raises(SolarmanagerRateLimit):
        await client.stream_user_v3()


async def test_401_triggers_single_token_refresh_retry(hass, aioclient_mock):
    """401 auf dem Stream → einmal Token erneuern + Retry, erst dann AuthError.

    Verhindert, dass ein transienter 401 sofort den Reauth-Flow auslöst.
    """
    aioclient_mock.post(LOGIN_URL, json=_TOKEN_RESPONSE)
    aioclient_mock.post(REFRESH_URL, json=_TOKEN_RESPONSE)
    aioclient_mock.get(STREAM_URL, status=401)

    client = _client(hass)
    await client.login()
    with pytest.raises(SolarmanagerAuthError):
        await client.stream_user_v3()

    # Aufrufe: 1× Login, 2× Stream (Original + Retry), 1× Refresh dazwischen
    methods_paths = [(m, u.path) for m, u, *_ in aioclient_mock.mock_calls]
    assert methods_paths.count(("GET", "/v3/users/SM1/data/stream")) == 2
    assert methods_paths.count(("POST", "/v1/oauth/refresh")) == 1


async def test_rejected_refresh_token_falls_back_to_full_login(hass, aioclient_mock):
    """Abgelehnter v1-Refresh-Token → Fallback auf vollen Login statt AuthError.

    Die gespeicherten Zugangsdaten können weiterhin gültig sein — ohne Fallback
    würde unnötig der Reauth-Flow ausgelöst.
    """
    aioclient_mock.post(LOGIN_URL, json=_TOKEN_RESPONSE)
    aioclient_mock.post(REFRESH_URL, status=401)
    aioclient_mock.get(STREAM_URL, json={"pW": 1})

    client = _client(hass)
    await client.login()
    client._exp_ts = 0.0  # Token als abgelaufen markieren → Refresh nötig

    data = await client.stream_user_v3()

    assert data == {"pW": 1}
    methods_paths = [(m, u.path) for m, u, *_ in aioclient_mock.mock_calls]
    assert methods_paths.count(("POST", "/v1/oauth/login")) == 2
    assert methods_paths.count(("POST", "/v1/oauth/refresh")) == 1
