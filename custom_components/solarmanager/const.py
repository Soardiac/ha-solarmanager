# const.py
from __future__ import annotations

DOMAIN = "solarmanager"

# Credentials / Config
CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_SM_ID = "sm_id"
CONF_API_KEY = "api_key"  # optional; nur nutzen, wenn du bewusst Basic-Auth statt OAuth brauchst

# Optionen
CONF_SCAN_INTERVAL = "scan_interval"
DEFAULT_SCAN = 10  # Sekunden – der v3-Stream liefert typischerweise iv≈10s

# API: eine gemeinsame Basis-URL für alles
CLOUD_BASE = "https://cloud.solar-manager.ch"
AUTH_LOGIN_PATH = "/v1/oauth/login"
AUTH_REFRESH_PATH = "/v1/oauth/refresh"
STREAM_PATH_TEMPLATE = "/v3/users/{sm_id}/data/stream"

# Plattformen
PLATFORMS = ["sensor", "number"]

# Branding (für device_info)
MANUFACTURER = "Solarmanager"
MODEL = "Cloud v3 stream"
