# api_client.py
from __future__ import annotations

import time
from typing import Any, Dict, Optional

import aiohttp


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

    async def login(self) -> None:
        """POST /v1/oauth/login → accessToken/refreshToken/expiresIn/tokenType."""
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
            self._exp_ts = time.time() + exp_sec - 30  # kleiner Sicherheits-Puffer

            if not self._access:
                raise SolarmanagerAuthError("No accessToken in response")

    async def _ensure_token(self) -> None:
        """Token rechtzeitig erneuern (POST /v1/oauth/refresh)."""
        if self._access and time.time() < self._exp_ts:
            return

        if not self._refresh:
            # Kein Refresh verfügbar → neu einloggen
            await self.login()
            return

        url = f"{self._base}/v1/oauth/refresh"
        async with self._s.post(url, json={"refreshToken": self._refresh}, timeout=30) as r:
            if r.status == 401:
                raise SolarmanagerAuthError("Refresh failed")
            if r.status >= 400:
                text = await r.text()
                raise SolarmanagerApiError(f"Refresh error {r.status}: {text}")

            data = await r.json()
            self._access = data.get("accessToken")
            # Einige Backends geben denselben Refresh zurück – wenn neu, übernehmen
            self._refresh = data.get("refreshToken", self._refresh)
            self._token_type = data.get("tokenType", self._token_type or "Bearer")
            exp_sec = int(data.get("expiresIn", 3600))
            self._exp_ts = time.time() + exp_sec - 30

            if not self._access:
                raise SolarmanagerAuthError("No accessToken after refresh")

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

        # Entweder Basic ODER Bearer – niemals beides senden!
        if self.api_key:
            headers = {"Authorization": f"Basic {self.api_key}", "accept": "application/json"}
            return await self._get_json(path, headers)

        # Bearer (Standard)
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

    async def put_battery_settings(self, sensor_id: str, payload: dict) -> None:
        """PUT /v2/control/battery/{sensorId} – setzt Modus/Parameter der Batterie.
        payload kann z.B. {"batteryMode": 1, "dischargeSocLimit": 30} sein.
        """
        await self._ensure_token()
        path = f"/v2/control/battery/{sensor_id}"
        url = f"{self._base}{path}"
        headers = self._bearer_headers()
        async with self._s.put(url, json=payload, headers=headers, timeout=30) as r:
            if r.status in (204, 200):
                return
            if r.status == 401:
                raise SolarmanagerAuthError("Unauthorized battery control")
            text = await r.text()
            raise SolarmanagerApiError(f"PUT {path} failed {r.status}: {text}")


