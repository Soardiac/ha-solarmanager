# api_client.py
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional

import aiohttp

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
LOCAL_TIMEOUT = aiohttp.ClientTimeout(total=10)


async def _check_status(r: aiohttp.ClientResponse, *, context: str) -> None:
    """HTTP-Status auf die passende Solarmanager-Exception mappen; wirft bei Fehler."""
    if r.status in (401, 403):
        raise SolarmanagerAuthError(f"{context} auth failed (HTTP {r.status})")
    if r.status == 429:
        raise SolarmanagerRateLimit("Rate limited")
    if r.status >= 400:
        text = await r.text()
        raise SolarmanagerApiError(f"{context} failed {r.status}: {text}")


def normalize_local_host(host: str) -> str:
    """Kanonischer Host ohne Scheme/Slash — für unique_id & Anzeige."""
    host = host.strip().rstrip("/")
    for prefix in ("http://", "https://"):
        if host.startswith(prefix):
            host = host[len(prefix):]
            break
    return host.lower()


class SolarmanagerAuthError(Exception):
    """Auth- oder Tokenfehler."""


class SolarmanagerRateLimit(Exception):
    """API-Rate-Limit überschritten (HTTP 429)."""


class SolarmanagerApiError(Exception):
    """Sonstiger API-Fehler (inkl. Netzwerkfehler/Timeouts)."""


class SolarmanagerCloud:
    """
    Minimaler Async-Client für die Solarmanager Cloud API.

    Erwartet EINE gemeinsame Base-URL (https://cloud.solar-manager.ch) für:
      - POST /v1/oauth/login
      - POST /v1/oauth/refresh
      - GET  /v3/users/{smId}/data/stream
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        base: str,
        email: str,
        password: str,
        sm_id: str,
        api_key: Optional[str] = None,  # optional: v3 API Key statt v1 Login
    ):
        self._s = session
        self._base = base.rstrip("/")
        self._email = email
        self._password = password
        self.sm_id = sm_id
        self.api_key = api_key

        # Token-Zustand
        self._access: Optional[str] = None
        self._refresh: Optional[str] = None
        self._token_type: str = "Bearer"
        self._exp_ts: float = 0.0  # Epoch-Sekunden
        self._auth_lock = asyncio.Lock()

    # -------------------- OAuth --------------------

    async def _post_auth(self, path: str, payload: dict) -> dict:
        """POST-Helper für Auth-Endpunkte mit Netzwerk-Fehler-Mapping."""
        url = f"{self._base}{path}"
        try:
            async with self._s.post(url, json=payload, timeout=REQUEST_TIMEOUT) as r:
                await _check_status(r, context=f"POST {path}")
                return await r.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise SolarmanagerApiError(f"POST {path} unreachable: {err}") from err

    async def _exchange_api_key(self) -> None:
        """POST /v3/auth/refresh — API Key als Refresh Token → Access Token (24h)."""
        _LOGGER.debug("Auth: using /v3/auth/refresh (API Key)")
        data = await self._post_auth(
            "/v3/auth/refresh",
            {"grant_type": "refresh_token", "refresh_token": self.api_key},
        )
        self._access = data.get("access_token")  # snake_case, anders als v1!
        exp_sec = int(data.get("expires_in", 86400))
        self._exp_ts = time.time() + exp_sec - 30
        if not self._access:
            raise SolarmanagerAuthError("No access_token in v3/auth/refresh response")

    async def _login_v1(self) -> None:
        """POST /v1/oauth/login (Fallback; gültig bis 30.06.2027)."""
        _LOGGER.debug("Auth: using /v1/oauth/login (email/password fallback)")
        data = await self._post_auth(
            "/v1/oauth/login", {"email": self._email, "password": self._password}
        )
        self._access = data.get("accessToken")
        self._refresh = data.get("refreshToken")
        self._token_type = data.get("tokenType", "Bearer")
        exp_sec = int(data.get("expiresIn", 3600))
        self._exp_ts = time.time() + exp_sec - 30
        if not self._access:
            raise SolarmanagerAuthError("No accessToken in response")

    async def _refresh_v1(self) -> None:
        """POST /v1/oauth/refresh (Fallback; gültig bis 30.06.2027)."""
        data = await self._post_auth("/v1/oauth/refresh", {"refreshToken": self._refresh})
        self._access = data.get("accessToken")
        self._refresh = data.get("refreshToken", self._refresh)
        self._token_type = data.get("tokenType", self._token_type or "Bearer")
        exp_sec = int(data.get("expiresIn", 3600))
        self._exp_ts = time.time() + exp_sec - 30
        if not self._access:
            raise SolarmanagerAuthError("No accessToken after refresh")

    async def login(self) -> None:
        """Authentifiziert: v3/auth/refresh wenn api_key gesetzt, sonst v1/oauth/login."""
        if self.api_key:
            await self._exchange_api_key()
        else:
            await self._login_v1()

    def _invalidate_token(self) -> None:
        self._access = None
        self._exp_ts = 0.0

    async def _ensure_token(self) -> None:
        """Token rechtzeitig erneuern (Lock verhindert parallele Refreshes)."""
        if self._access and time.time() < self._exp_ts:
            return
        async with self._auth_lock:
            # Ein anderer Task kann den Token inzwischen erneuert haben
            if self._access and time.time() < self._exp_ts:
                return
            if self.api_key:
                await self._exchange_api_key()
            elif self._refresh:
                await self._refresh_v1()
            else:
                await self._login_v1()

    def _bearer_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"{self._token_type} {self._access}",
            "accept": "application/json",
        }

    # -------------------- Authentifizierte Requests --------------------

    async def _authed_request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
        parse_json: bool = True,
    ) -> Any:
        """Request mit Bearer-Token, Netzwerk-Fehler-Mapping und einmaligem
        Retry bei 401 (Token kann serverseitig invalidiert worden sein)."""
        url = f"{self._base}/{path.lstrip('/')}"
        await self._ensure_token()
        for attempt in (0, 1):
            try:
                async with self._s.request(
                    method,
                    url,
                    json=json,
                    params=params,
                    headers=self._bearer_headers(),
                    timeout=REQUEST_TIMEOUT,
                ) as r:
                    if r.status == 401 and attempt == 0:
                        _LOGGER.debug(
                            "401 on %s %s — refreshing token, retrying once",
                            method,
                            path,
                        )
                        self._invalidate_token()
                        await self._ensure_token()
                        continue
                    await _check_status(r, context=f"{method} {path}")
                    if not parse_json or r.status == 204:
                        return None
                    return await r.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                raise SolarmanagerApiError(f"{method} {path} unreachable: {err}") from err
        return None  # nicht erreichbar (Schleife endet immer mit return/raise)

    # -------------------- v3 Stream --------------------

    async def stream_user_v3(self) -> Dict[str, Any]:
        """
        GET /v3/users/{smId}/data/stream
        Liefert Felder wie: v, t, iv, pW, cW, iW, eW, bcW, bdW, soc, ... und devices[].
        """
        return await self._authed_request("GET", f"/v3/users/{self.sm_id}/data/stream")

    async def list_devices(self) -> list[dict]:
        """GET /v1/info/sensors/{smId} → Liste der Geräte mit _id und (tag.name | name)."""
        return await self._authed_request("GET", f"/v1/info/sensors/{self.sm_id}")

    async def _put_control(self, path: str, payload: dict) -> None:
        """Generischer PUT-Helper für alle /control/… Endpunkte."""
        await self._authed_request("PUT", path, json=payload, parse_json=False)

    async def put_battery_settings(self, sensor_id: str, payload: dict) -> None:
        """PUT /v2/control/battery/{sensorId}"""
        await self._put_control(f"/v2/control/battery/{sensor_id}", payload)

    async def put_car_charger_mode(self, sensor_id: str, payload: dict) -> None:
        """PUT /v1/control/car-charger/{sensorId}"""
        await self._put_control(f"/v1/control/car-charger/{sensor_id}", payload)

    async def put_heat_pump_mode(self, sensor_id: str, payload: dict) -> None:
        """PUT /v1/control/heat-pump/{sensorId}"""
        await self._put_control(f"/v1/control/heat-pump/{sensor_id}", payload)

    async def put_water_heater_mode(self, sensor_id: str, payload: dict) -> None:
        """PUT /v1/control/water-heater/{sensorId}"""
        await self._put_control(f"/v1/control/water-heater/{sensor_id}", payload)

    async def put_smart_plug_mode(self, sensor_id: str, payload: dict) -> None:
        """PUT /v1/control/smart-plug/{sensorId}"""
        await self._put_control(f"/v1/control/smart-plug/{sensor_id}", payload)

    async def put_switch_mode(self, sensor_id: str, payload: dict) -> None:
        """PUT /v1/control/switch/{sensorId}"""
        await self._put_control(f"/v1/control/switch/{sensor_id}", payload)

    async def put_v2x_mode(self, sensor_id: str, payload: dict) -> None:
        """PUT /v2/control/v2x/{sensorId}"""
        await self._put_control(f"/v2/control/v2x/{sensor_id}", payload)

    async def put_inverter_settings(self, sensor_id: str, payload: dict) -> None:
        """PUT /v1/control/inverter/{sensorId}"""
        await self._put_control(f"/v1/control/inverter/{sensor_id}", payload)

    async def get_gateway_statistics(
        self, from_dt: str, to_dt: str, accuracy: str = "high"
    ) -> dict:
        """GET /v1/statistics/gateways/{smId}?from=...&to=...&accuracy=...

        accuracy: 'low' (>1 month), 'medium' (<=1 month), 'high' (<=1 week)
        Returns: production, consumption, selfConsumption (Wh), selfConsumptionRate, autarchyDegree (%)
        """
        return await self._authed_request(
            "GET",
            f"/v1/statistics/gateways/{self.sm_id}",
            params={"from": from_dt, "to": to_dt, "accuracy": accuracy},
        )


class SolarmanagerLocal:
    """Read-only client for the local Solar Manager REST API (v2)."""

    def __init__(
        self,
        host: str,
        session: aiohttp.ClientSession,
        *,
        scheme: str = "http",
        api_key: str | None = None,
    ) -> None:
        self._base = f"{scheme}://{normalize_local_host(host)}"
        self._s = session
        self._api_key = api_key

    def _headers(self) -> dict:
        return {"X-API-Key": self._api_key} if self._api_key else {}

    async def get_point(self) -> dict:
        """GET /v2/point → aktueller Datenpunkt (identisches Feldformat wie Cloud-Stream)."""
        try:
            async with self._s.get(
                f"{self._base}/v2/point",
                headers=self._headers(),
                ssl=False,
                timeout=LOCAL_TIMEOUT,
            ) as r:
                await _check_status(r, context="GET /v2/point")
                return await r.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise SolarmanagerApiError(f"Local API unreachable: {err}") from err

    async def list_devices(self) -> list[dict]:
        """GET /v2/devices → normalisiert auf Cloud-Format {_id, name, type}."""
        try:
            async with self._s.get(
                f"{self._base}/v2/devices",
                headers=self._headers(),
                ssl=False,
                timeout=LOCAL_TIMEOUT,
            ) as r:
                await _check_status(r, context="GET /v2/devices")
                data = await r.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise SolarmanagerApiError(f"Local devices unreachable: {err}") from err
        return [
            {
                "_id": d["deviceId"],
                "name": d.get("description") or d.get("name") or d["deviceId"],
                "type": d.get("type", ""),
            }
            for d in (data if isinstance(data, list) else [])
            if isinstance(d, dict) and d.get("deviceId")
        ]
