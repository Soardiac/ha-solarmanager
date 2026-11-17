"""Diagnose-Export: Zugangsdaten und Netzwerk-/Geräte-Kennungen werden geschwärzt.

Nutzer laden die Datei in öffentliche GitHub-Issues hoch.
"""
import json

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solarmanager.const import (
    CONF_API_KEY,
    CONF_CLOUD_API_KEY,
    CONF_HOST,
    CONF_MODE,
    CONF_SCHEME,
    CONF_SM_ID,
    DOMAIN,
    MODE_LOCAL,
)
from custom_components.solarmanager.coordinator import SolarmanagerCoordinator
from custom_components.solarmanager.diagnostics import (
    async_get_config_entry_diagnostics,
)

REDACTED = "**REDACTED**"
SECRETS = ("192.0.2.10", "192.0.2.20", "02:00:00:00:00:01", "ws://ocpp.example/ABC", "12345678", "SM-SECRET")


async def test_diagnostics_redacts_network_and_charger_ids(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_MODE: MODE_LOCAL,
            CONF_HOST: "192.0.2.10",
            CONF_SCHEME: "https",
            CONF_API_KEY: None,
            CONF_SM_ID: "SM-SECRET",
            CONF_CLOUD_API_KEY: "cloud-key",
        },
        unique_id="local_192.0.2.10",
    )
    entry.add_to_hass(hass)
    coord = SolarmanagerCoordinator(hass, entry)
    coord.data = {"devices": [{"_id": "bat", "power": 1, "signal": "connected"}]}
    coord.device_meta = {
        "bat": {
            "name": "Reserva",
            "type": "battery",
            "raw": {"_id": "bat", "type": "battery", "ip": "192.0.2.20", "mac": "02:00:00:00:00:01",
                    "data": {"batteryMode": 1}},
        },
        "wb": {
            "name": "Wattpilot",
            "type": "carcharging",
            "raw": {"_id": "wb", "type": "carcharging",
                    "data": {"OcppUrl": "ws://ocpp.example/ABC", "OCPPChargerID": "12345678", "chargingMode": 0}},
        },
    }
    entry.runtime_data = coord

    diag = await async_get_config_entry_diagnostics(hass, entry)

    dump = json.dumps(diag)
    for secret in SECRETS:
        assert secret not in dump
    assert diag["config_entry"]["data"][CONF_HOST] == REDACTED
    bat = diag["device_metadata"]["bat"]["raw"]
    assert bat["ip"] == REDACTED and bat["mac"] == REDACTED
    wb = diag["device_metadata"]["wb"]["raw"]["data"]
    assert wb["OcppUrl"] == REDACTED and wb["OCPPChargerID"] == REDACTED
    # Nutzwerte für die Fehlersuche bleiben erhalten
    assert diag["config_entry"]["data"][CONF_SCHEME] == "https"
    assert wb["chargingMode"] == 0
    assert diag["device_metadata"]["bat"]["name"] == "Reserva"
    assert diag["device_metadata"]["bat"]["raw"]["data"] == {"batteryMode": 1}
