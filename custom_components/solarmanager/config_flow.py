# config_flow.py
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api_client import (
    SolarmanagerAuthError,
    SolarmanagerApiError,
    SolarmanagerCloud,
    SolarmanagerLocal,
    normalize_local_host,
)
from .const import (
    CLOUD_BASE,
    CONF_API_KEY,
    CONF_EMAIL,
    CONF_HOST,
    CONF_MODE,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_SCHEME,
    CONF_SM_ID,
    DEFAULT_SCAN,
    DOMAIN,
    MODE_CLOUD,
    MODE_LOCAL,
)

_LOGGER = logging.getLogger(__name__)


def _schema_cloud(defaults: dict | None = None) -> vol.Schema:
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
        """Mode selector: Cloud or Local."""
        if user_input is not None:
            if user_input[CONF_MODE] == MODE_LOCAL:
                return await self.async_step_local()
            return await self.async_step_cloud()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_MODE, default=MODE_CLOUD): vol.In([MODE_CLOUD, MODE_LOCAL]),
            }),
        )

    async def async_step_cloud(self, user_input=None) -> FlowResult:
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
                await client.login()
                await client.stream_user_v3()

                await self.async_set_unique_id(f"{DOMAIN}_{user_input[CONF_SM_ID]}")
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"Solarmanager {user_input[CONF_SM_ID]}",
                    data={CONF_MODE: MODE_CLOUD, **user_input},
                )
            except SolarmanagerAuthError:
                errors["base"] = "auth"
            except SolarmanagerApiError:
                errors["base"] = "cannot_connect"
            except Exception:
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="cloud",
            data_schema=_schema_cloud(user_input),
            errors=errors,
        )

    async def async_step_local(self, user_input=None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            host = normalize_local_host(user_input[CONF_HOST])
            scheme = user_input[CONF_SCHEME]
            api_key = user_input.get(CONF_API_KEY) or None
            session = async_get_clientsession(self.hass)
            client = SolarmanagerLocal(host, session, scheme=scheme, api_key=api_key)
            try:
                await client.get_point()
            except SolarmanagerApiError:
                errors["base"] = "cannot_connect"
            except Exception:
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(f"local_{host}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Solarmanager Local ({host})",
                    data={
                        CONF_MODE: MODE_LOCAL,
                        CONF_HOST: host,
                        CONF_SCHEME: scheme,
                        CONF_API_KEY: api_key,
                    },
                )

        return self.async_show_form(
            step_id="local",
            data_schema=vol.Schema({
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_SCHEME, default="http"): vol.In(["http", "https"]),
                vol.Optional(CONF_API_KEY, default=""): str,
            }),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> FlowResult:
        if entry_data.get(CONF_MODE) == MODE_LOCAL:
            return self.async_abort(reason="reauth_not_supported")
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None) -> FlowResult:
        reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        errors: dict[str, str] = {}

        if user_input is not None:
            session = async_get_clientsession(self.hass)
            try:
                client = SolarmanagerCloud(
                    session,
                    base=CLOUD_BASE,
                    email=user_input[CONF_EMAIL],
                    password=user_input[CONF_PASSWORD],
                    sm_id=reauth_entry.data[CONF_SM_ID],
                    api_key=user_input.get(CONF_API_KEY) or None,
                )
                await client.login()
            except SolarmanagerAuthError:
                errors["base"] = "auth"
            except SolarmanagerApiError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during reauth")
                errors["base"] = "unknown"
            else:
                self.hass.config_entries.async_update_entry(
                    reauth_entry,
                    data={
                        **reauth_entry.data,
                        CONF_EMAIL: user_input[CONF_EMAIL],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_API_KEY: user_input.get(CONF_API_KEY) or None,
                    },
                )
                await self.hass.config_entries.async_reload(reauth_entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({
                vol.Optional(CONF_EMAIL, default=reauth_entry.data.get(CONF_EMAIL, "")): str,
                vol.Optional(CONF_PASSWORD, default=""): str,
                vol.Optional(CONF_API_KEY, default=reauth_entry.data.get(CONF_API_KEY, "")): str,
            }),
            errors=errors,
            description_placeholders={"sm_id": reauth_entry.data.get(CONF_SM_ID, "")},
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        return SolarmanagerOptionsFlow()


class SolarmanagerOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None) -> FlowResult:
        is_local = self.config_entry.data.get(CONF_MODE) == MODE_LOCAL
        errors: dict[str, str] = {}

        if user_input is not None:
            if not is_local:
                api_key = (user_input.get(CONF_API_KEY) or "").strip() or None
                if api_key:
                    session = async_get_clientsession(self.hass)
                    current = self.config_entry.data
                    client = SolarmanagerCloud(
                        session,
                        base=CLOUD_BASE,
                        email=current.get(CONF_EMAIL, ""),
                        password=current.get(CONF_PASSWORD, ""),
                        sm_id=current[CONF_SM_ID],
                        api_key=api_key,
                    )
                    try:
                        await client.login()
                    except SolarmanagerAuthError:
                        errors["base"] = "auth"
                    except SolarmanagerApiError:
                        errors["base"] = "cannot_connect"
                    except Exception:
                        _LOGGER.exception("Unexpected error validating API key")
                        errors["base"] = "unknown"
                    else:
                        self.hass.config_entries.async_update_entry(
                            self.config_entry,
                            data={**self.config_entry.data, CONF_API_KEY: api_key},
                        )

            if not errors:
                return self.async_create_entry(
                    title="", data={CONF_SCAN_INTERVAL: user_input[CONF_SCAN_INTERVAL]}
                )

        if is_local:
            schema = vol.Schema({
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN),
                ): int,
            })
            return self.async_show_form(step_id="init", data_schema=schema, errors=errors)

        has_api_key = bool(self.config_entry.data.get(CONF_API_KEY))
        schema = vol.Schema({
            vol.Optional(CONF_API_KEY, default=""): str,
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN),
            ): int,
        })
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
            description_placeholders={"api_key_status": "gesetzt ✓" if has_api_key else "nicht gesetzt"},
        )
