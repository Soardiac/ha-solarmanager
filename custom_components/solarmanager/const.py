# const.py
from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "solarmanager"

# Credentials / Config
CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_SM_ID = "sm_id"
CONF_API_KEY = "api_key"  # optional; nur nutzen, wenn du bewusst Basic-Auth statt OAuth brauchst

# E-Mail/Passwort-Login (v1) wird von Solar Manager zu diesem Datum abgeschaltet.
PASSWORD_AUTH_DEADLINE = "30.06.2027"

# Optionen
CONF_SCAN_INTERVAL = "scan_interval"
DEFAULT_SCAN = 10  # Sekunden – der v3-Stream liefert typischerweise iv≈10s
# PV-Generatorleistung (kWp, Summe der Module) — überschreibt den Wert aus
# Solar Manager (/v1/info/gateway → settings.kWp). Leer = API-Wert verwenden.
CONF_GENERATOR_KWP = "generator_kwp"

# API: eine gemeinsame Basis-URL für alles
CLOUD_BASE = "https://cloud.solar-manager.ch"

# Plattformen
PLATFORMS = [
    Platform.SENSOR,
    Platform.NUMBER,
    Platform.BINARY_SENSOR,
    Platform.SELECT,
    Platform.DATETIME,
]

# Verbindungsmodus
CONF_HOST = "host"
CONF_SCHEME = "scheme"
CONF_MODE = "mode"
MODE_CLOUD = "cloud"
MODE_LOCAL = "local"

# Lokaler Modus mit Cloud-Ergänzung: Lesen bleibt lokal, Schreiben und reine
# Cloud-Daten (Geräte-Einstellungen, Prognose, kWp) laufen über die Cloud.
# Schalter in den Options, Zugangsdaten in data — getrennt vom lokalen API-Key.
CONF_CLOUD_EXTENSION = "cloud_extension"
CONF_CLOUD_API_KEY = "cloud_api_key"
# Lokaler API-Key, im Cloud-Modus nur gemerkt — für den Rückwechsel auf Lokal
CONF_LOCAL_API_KEY = "local_api_key"

# Branding (für device_info)
MANUFACTURER = "Solarmanager"
# Titel des Config-Entries und Name des Site-Geräts -- unabhängig vom Modus
ENTRY_TITLE = "Solar Manager"
MODEL = "Cloud v3 stream"
MODEL_LOCAL = "Local API v2"
