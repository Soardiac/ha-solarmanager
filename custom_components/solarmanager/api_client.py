# api_client.py
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

import aiohttp

_LOGGER = logging.getLogger(__name__)


class SolarmanagerAuthError(Exception):
    """Auth- oder Tokenfehler."""


class SolarmanagerRateLimit(Exception):
    """API-Rate-Limit überschritten (HTTP 429)."""


class SolarmanagerApiError(Exception):
    """Sonstiger API-Fehler."""


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
        api_key: Optional[str] = None,  # optional: nur für Basic-Auth beim Stream
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

    # -------------------- OAuth --------------------

    async def _exchange_api_key(self) -> None:
        """POST /v3/auth/refresh — API Key als Refresh Token → Access Token (24h)."""
        _LOGGER.debug("Auth: using /v3/auth/refresh (API Key)")
        url = f"{self._base}/v3/auth/refresh"
        async with self._s.post(
            url,
            json={"grant_type": "refresh_token", "refresh_token": self.api_key},
            timeout=30,
        ) as r:
            if r.status == 401:
                raise SolarmanagerAuthError("Invalid API key")
            if r.status >= 400:
                text = await r.text()
                raise SolarmanagerApiError(f"Auth failed {r.status}: {text}")
            data = await r.json()
            self._access = data.get("access_token")  # snake_case, anders als v1!
            exp_sec = int(data.get("expires_in", 86400))
            self._exp_ts = time.time() + exp_sec - 30
            if not self._access:
                raise SolarmanagerAuthError("No access_token in v3/auth/refresh response")

    async def _login_v1(self) -> None:
        """POST /v1/oauth/login (Fallback; gültig bis 30.06.2027)."""
        _LOGGER.debug("Auth: using /v1/oauth/login (email/password fallback)")
        url = f"{self._base}/v1/oauth/login"
        async with self._s.post(
            url,
            json={"email": self._email, "password": self._password},
            timeout=30,
        ) as r:
            if r.status == 401:
                raise SolarmanagerAuthError("Invalid credentials")
            if r.status >= 400:
                text = await r.text()
                raise SolarmanagerApiError(f"Login failed {r.status}: {text}")
            data = await r.json()
            self._access = data.get("accessToken")
            self._refresh = data.get("refreshToken")
            self._token_type = data.get("tokenType", "Bearer")
            exp_sec = int(data.get("expiresIn", 3600))
            self._exp_ts = time.time() + exp_sec - 30
            if not self._access:
                raise SolarmanagerAuthError("No accessToken in response")

    async def _refresh_v1(self) -> None:
        """POST /v1/oauth/refresh (Fallback; gültig bis 30.06.2027)."""
        url = f"{self._base}/v1/oauth/refresh"
        async with self._s.post(url, json={"refreshToken": self._refresh}, timeout=30) as r:
            if r.status == 401:
                raise SolarmanagerAuthError("Refresh failed")
            if r.status >= 400:
                text = await r.text()
                raise SolarmanagerApiError(f"Refresh error {r.status}: {text}")
            data = await r.json()
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

    async def _ensure_token(self) -> None:
        """Token rechtzeitig erneuern."""
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

    async def _get_json(self, path: str, headers: Dict[str, str]) -> Dict[str, Any]:
        """GET helper mit Fehlerbehandlung."""
        url = f"{self._base}/{path.lstrip('/')}"
        async with self._s.get(url, headers=headers, timeout=30) as r:
            if r.status == 401:
                raise SolarmanagerAuthError("Unauthorized")
            if r.status == 429:
                raise SolarmanagerRateLimit("Rate limited")
            if r.status >= 400:
                text = await r.text()
                raise SolarmanagerApiError(f"GET {path} failed {r.status}: {text}")
            return await r.json()

    # -------------------- v3 Stream --------------------

    async def stream_user_v3(self) -> Dict[str, Any]:
        """
        GET /v3/users/{smId}/data/stream
        Liefert Felder wie: v, t, iv, pW, cW, iW, eW, bcW, bdW, soc, ... und devices[].
        """
        path = f"/v3/users/{self.sm_id}/data/stream"

        await self._ensure_token()
        return await self._get_json(path, self._bearer_headers())
        
    async def list_sensors(self) -> list[dict]:
        """GET /v1/info/sensors/{smId} → Liste der Geräte/Sensoren mit Namen/Typ."""
        await self._ensure_token()
        return await self._get_json(f"/v1/info/sensors/{self.sm_id}", self._bearer_headers())

    async def sensor_detail(self, sensor_id: str) -> dict:
        """GET /v1/info/sensor/{sensor_id} → Detailinfos (optional)."""
        await self._ensure_token()
        return await self._get_json(f"/v1/info/sensor/{sensor_id}", self._bearer_headers())
        
    async def list_devices(self) -> list[dict]:
        """GET /v1/info/sensors/{smId} → Liste der Geräte mit _id und (tag.name | name)."""
        await self._ensure_token()
        return await self._get_json(f"/v1/info/sensors/{self.sm_id}", self._bearer_headers())

    async def _put_control(self, path: str, payload: dict) -> None:
        """Generischer PUT-Helper für alle /control/… Endpunkte."""
        await self._ensure_token()
        url = f"{self._base}{path}"
        async with self._s.put(url, json=payload, headers=self._bearer_headers(), timeout=30) as r:
            if r.status in (200, 204):
                return
            if r.status == 401:
                raise SolarmanagerAuthError(f"Unauthorized: {path}")
            text = await r.text()
            raise SolarmanagerApiError(f"PUT {path} failed {r.status}: {text}")

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
        await self._ensure_token()
        url = f"{self._base}/v1/statistics/gateways/{self.sm_id}"
        params = {"from": from_dt, "to": to_dt, "accuracy": accuracy}
        async with self._s.get(
            url, headers=self._bearer_headers(), params=params, timeout=30
        ) as r:
            if r.status == 401:
                raise SolarmanagerAuthError("Unauthorized")
            if r.status == 429:
                raise SolarmanagerRateLimit("Rate limited")
            if r.status >= 400:
                text = await r.text()
                raise SolarmanagerApiError(f"GET statistics failed {r.status}: {text}")
            return await r.json()


class SolarmanagerLocal:
    """Read-only client for the local Solar Manager REST API (v2)."""

    def __init__(self, host: str, session: aiohttp.ClientSession) -> None:
        self._base = f"http://{host}"
        self._s = session

    async def get_point(self) -> dict:
        """GET /v2/point → aktueller Datenpunkt (identisches Feldformat wie Cloud-Stream)."""
        try:
            async with self._s.get(
                f"{self._base}/v2/point", ssl=False, timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                r.raise_for_status()
                return await r.json()
        except aiohttp.ClientError as err:
            raise SolarmanagerApiError(f"Local API unreachable: {err}") from err

    async def list_devices(self) -> list[dict]:
        """GET /v2/devices → normalisiert auf Cloud-Format {_id, name, type}."""
        try:
            async with self._s.get(
                f"{self._base}/v2/devices", ssl=False, timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                r.raise_for_status()
                data = await r.json()
        except aiohttp.ClientError as err:
            raise SolarmanagerApiError(f"Local devices unreachable: {err}") from err
        return [
            {
                "_id": d["deviceId"],
                "name": d.get("description") or d.get("name") or d["deviceId"],
                "type": d.get("type", ""),
            }
            for d in (data if isinstance(data, list) else [])
        ]


