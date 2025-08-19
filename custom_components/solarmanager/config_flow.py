# config_flow.py
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    DOMAIN,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_SM_ID,
    CONF_API_KEY,          # optional; kann entfallen, wenn du nur Bearer nutzt
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN,
    CLOUD_BASE,
)
from .api_client import (
    SolarmanagerCloud,
    SolarmanagerAuthError,
    SolarmanagerApiError,
)


def _schema_user(defaults: dict | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema({
        vol.Required(CONF_EMAIL, default=defaults.get(CONF_EMAIL, "")): str,
        vol.Required(CONF_PASSWORD, default=defaults.get(CONF_PASSWORD, "")): str,
        vol.Required(CONF_SM_ID, default=defaults.get(CONF_SM_ID, "")): str,
        vol.Optional(CONF_API_KEY, default=defaults.get(CONF_API_KEY, "")): str,
    })


class SolarmanagerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input:
            session = async_get_clientsession(self.hass)

            client = SolarmanagerCloud(
                session,
                base=CLOUD_BASE,
                email=user_input[CONF_EMAIL],
                password=user_input[CONF_PASSWORD],
                sm_id=user_input[CONF_SM_ID],
                api_key=(user_input.get(CONF_API_KEY) or None),
            )
            try:
                # Login prüfen
                await client.login()
                # smId validieren (leichtgewichtig): einmal Stream holen
                await client.stream_user_v3()

                title = f"Solarmanager {user_input[CONF_SM_ID]}"
                # Einmal pro smId verhindern
                await self.async_set_unique_id(f"{DOMAIN}_{user_input[CONF_SM_ID]}")
                self._abort_if_unique_id_configured()

                return self.async_create_entry(title=title, data=user_input)

            except SolarmanagerAuthError:
                errors["base"] = "auth"
            except SolarmanagerApiError:
                errors["base"] = "cannot_connect"
            except Exception:
                errors["base"] = "unknown"

        return self.async_show_form(step_id="user", data_schema=_schema_user(user_input), errors=errors)

    @staticmethod
    def async_get_options_flow(config_entry):
        return SolarmanagerOptionsFlow(config_entry)


class SolarmanagerOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        schema = vol.Schema({
            vol.Optional(CONF_SCAN_INTERVAL, default=self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN)): int,
        })
        return self.async_show_form(step_id="init", data_schema=schema)
