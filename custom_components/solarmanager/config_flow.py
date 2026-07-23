# config_flow.py
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

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

PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
EMAIL_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL))


def _schema_cloud(defaults: dict | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema({
        vol.Required(CONF_EMAIL, default=defaults.get(CONF_EMAIL, "")): EMAIL_SELECTOR,
        vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
        vol.Required(CONF_SM_ID, default=defaults.get(CONF_SM_ID, "")): str,
        vol.Optional(CONF_API_KEY, default=""): PASSWORD_SELECTOR,
    })


def _schema_local(defaults: dict | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema({
        vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
        vol.Required(CONF_SCHEME, default=defaults.get(CONF_SCHEME, "http")): vol.In(["http", "https"]),
        vol.Optional(CONF_API_KEY, default=""): PASSWORD_SELECTOR,
    })


async def _validate_cloud(
    hass,
    *,
    email: str,
    password: str,
    sm_id: str,
    api_key: str | None,
    check_stream: bool = False,
) -> None:
    """Cloud-Zugangsdaten prüfen (Login, optional Stream-Abruf); wirft bei Fehler."""
    session = async_get_clientsession(hass)
    client = SolarmanagerCloud(
        session,
        base=CLOUD_BASE,
        email=email,
        password=password,
        sm_id=sm_id,
        api_key=api_key,
    )
    await client.login()
    if check_stream:
        await client.stream_user_v3()


async def _validate_local(hass, *, host: str, scheme: str, api_key: str | None) -> None:
    """Lokale Zugangsdaten prüfen (GET /v2/point); wirft bei Fehler."""
    session = async_get_clientsession(hass)
    client = SolarmanagerLocal(host, session, scheme=scheme, api_key=api_key)
    await client.get_point()


async def _validate_and_map_errors(coro) -> dict[str, str]:
    """Validierungs-Coroutine ausführen und Exceptions auf ein Errors-Dict mappen."""
    try:
        await coro
    except SolarmanagerAuthError:
        return {"base": "auth"}
    except SolarmanagerApiError:
        return {"base": "cannot_connect"}
    except Exception:
        _LOGGER.exception("Unexpected error validating Solarmanager connection")
        return {"base": "unknown"}
    return {}


async def _cloud_errors(
    hass,
    *,
    email: str,
    password: str,
    sm_id: str,
    api_key: str | None,
    check_stream: bool = False,
) -> dict[str, str]:
    """Cloud-Zugangsdaten validieren; liefert Errors-Dict (leer bei Erfolg)."""
    return await _validate_and_map_errors(
        _validate_cloud(
            hass,
            email=email,
            password=password,
            sm_id=sm_id,
            api_key=api_key,
            check_stream=check_stream,
        )
    )


async def _local_errors(hass, *, host: str, scheme: str, api_key: str | None) -> dict[str, str]:
    """Lokale Verbindung validieren; liefert Errors-Dict (leer bei Erfolg)."""
    return await _validate_and_map_errors(
        _validate_local(hass, host=host, scheme=scheme, api_key=api_key)
    )


class SolarmanagerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
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

    async def async_step_cloud(self, user_input=None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input:
            errors = await _cloud_errors(
                self.hass,
                email=user_input[CONF_EMAIL],
                password=user_input[CONF_PASSWORD],
                sm_id=user_input[CONF_SM_ID],
                api_key=(user_input.get(CONF_API_KEY) or None),
                check_stream=True,
            )
            if not errors:
                await self.async_set_unique_id(f"{DOMAIN}_{user_input[CONF_SM_ID]}")
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"Solarmanager {user_input[CONF_SM_ID]}",
                    data={CONF_MODE: MODE_CLOUD, **user_input},
                )

        return self.async_show_form(
            step_id="cloud",
            data_schema=_schema_cloud(user_input),
            errors=errors,
        )

    async def async_step_local(self, user_input=None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            host = normalize_local_host(user_input[CONF_HOST])
            scheme = user_input[CONF_SCHEME]
            api_key = user_input.get(CONF_API_KEY) or None
            errors = await _local_errors(self.hass, host=host, scheme=scheme, api_key=api_key)
            if not errors:
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
            data_schema=_schema_local(user_input),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Re-Auth
    # ------------------------------------------------------------------

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        if entry_data.get(CONF_MODE) == MODE_LOCAL:
            return await self.async_step_reauth_local()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None) -> ConfigFlowResult:
        reauth_entry = self._get_reauth_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            # Leere Eingaben überschreiben die gespeicherten Werte NICHT —
            # sonst wäre nach einem API-Key-only-Reauth der v1-Fallback tot.
            email = user_input.get(CONF_EMAIL) or reauth_entry.data.get(CONF_EMAIL, "")
            password = user_input.get(CONF_PASSWORD) or reauth_entry.data.get(CONF_PASSWORD, "")
            api_key = user_input.get(CONF_API_KEY) or reauth_entry.data.get(CONF_API_KEY)
            errors = await _cloud_errors(
                self.hass,
                email=email,
                password=password,
                sm_id=reauth_entry.data[CONF_SM_ID],
                api_key=api_key or None,
            )
            if not errors:
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data_updates={
                        CONF_EMAIL: email,
                        CONF_PASSWORD: password,
                        CONF_API_KEY: api_key or None,
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({
                vol.Optional(CONF_EMAIL, default=reauth_entry.data.get(CONF_EMAIL, "")): EMAIL_SELECTOR,
                vol.Optional(CONF_PASSWORD, default=""): PASSWORD_SELECTOR,
                vol.Optional(CONF_API_KEY, default=""): PASSWORD_SELECTOR,
            }),
            errors=errors,
            description_placeholders={"sm_id": reauth_entry.data.get(CONF_SM_ID, "")},
        )

    async def async_step_reauth_local(self, user_input=None) -> ConfigFlowResult:
        """Lokaler Modus: API-Key wurde vom Gateway abgelehnt → neuen Key abfragen."""
        reauth_entry = self._get_reauth_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            # Leer = Gateway verlangt (nicht mehr) einen Key
            api_key = (user_input.get(CONF_API_KEY) or "").strip() or None
            errors = await _local_errors(
                self.hass,
                host=reauth_entry.data[CONF_HOST],
                scheme=reauth_entry.data.get(CONF_SCHEME, "http"),
                api_key=api_key,
            )
            if not errors:
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data_updates={CONF_API_KEY: api_key},
                )

        return self.async_show_form(
            step_id="reauth_local",
            data_schema=vol.Schema({
                vol.Optional(CONF_API_KEY, default=""): PASSWORD_SELECTOR,
            }),
            errors=errors,
            description_placeholders={"host": reauth_entry.data.get(CONF_HOST, "")},
        )

    # ------------------------------------------------------------------
    # Reconfigure
    # ------------------------------------------------------------------

    def _other_entry_has_unique_id(self, entry: ConfigEntry, unique_id: str) -> bool:
        return any(
            other.entry_id != entry.entry_id and other.unique_id == unique_id
            for other in self.hass.config_entries.async_entries(DOMAIN)
        )

    async def async_step_reconfigure(self, user_input=None) -> ConfigFlowResult:
        """Modus-Selector: gleicher Modus -> In-Place-Reconfigure, sonst Moduswechsel."""
        entry = self._get_reconfigure_entry()
        current_mode = entry.data.get(CONF_MODE, MODE_CLOUD)

        if user_input is not None:
            target = user_input[CONF_MODE]
            if target == current_mode:
                if target == MODE_LOCAL:
                    return await self.async_step_reconfigure_local()
                return await self.async_step_reconfigure_cloud()
            if target == MODE_LOCAL:
                return await self.async_step_switch_to_local()
            return await self.async_step_switch_to_cloud()

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema({
                vol.Required(CONF_MODE, default=current_mode): vol.In([MODE_CLOUD, MODE_LOCAL]),
            }),
        )

    async def async_step_switch_to_cloud(self, user_input=None) -> ConfigFlowResult:
        """Moduswechsel Lokal -> Cloud: neue Zugangsdaten, kompletter Datenersatz."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            new_uid = f"{DOMAIN}_{user_input[CONF_SM_ID]}"
            if self._other_entry_has_unique_id(entry, new_uid):
                return self.async_abort(reason="already_configured")

            errors = await _cloud_errors(
                self.hass,
                email=user_input[CONF_EMAIL],
                password=user_input[CONF_PASSWORD],
                sm_id=user_input[CONF_SM_ID],
                api_key=(user_input.get(CONF_API_KEY) or None),
                check_stream=True,
            )
            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=new_uid,
                    title=f"Solarmanager {user_input[CONF_SM_ID]}",
                    data={CONF_MODE: MODE_CLOUD, **user_input},
                )

        return self.async_show_form(
            step_id="switch_to_cloud",
            data_schema=_schema_cloud(),
            errors=errors,
        )

    async def async_step_switch_to_local(self, user_input=None) -> ConfigFlowResult:
        """Moduswechsel Cloud -> Lokal: neue Zugangsdaten, kompletter Datenersatz."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            host = normalize_local_host(user_input[CONF_HOST])
            scheme = user_input[CONF_SCHEME]
            api_key = user_input.get(CONF_API_KEY) or None

            new_uid = f"local_{host}"
            if self._other_entry_has_unique_id(entry, new_uid):
                return self.async_abort(reason="already_configured")

            errors = await _local_errors(self.hass, host=host, scheme=scheme, api_key=api_key)
            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=new_uid,
                    title=f"Solarmanager Local ({host})",
                    data={
                        CONF_MODE: MODE_LOCAL,
                        CONF_HOST: host,
                        CONF_SCHEME: scheme,
                        CONF_API_KEY: api_key,
                    },
                )

        return self.async_show_form(
            step_id="switch_to_local",
            data_schema=_schema_local(),
            errors=errors,
        )

    async def async_step_reconfigure_cloud(self, user_input=None) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input.get(CONF_EMAIL) or entry.data.get(CONF_EMAIL, "")
            password = user_input.get(CONF_PASSWORD) or entry.data.get(CONF_PASSWORD, "")
            sm_id = user_input.get(CONF_SM_ID) or entry.data.get(CONF_SM_ID, "")
            api_key = user_input.get(CONF_API_KEY) or entry.data.get(CONF_API_KEY)

            new_uid = f"{DOMAIN}_{sm_id}"
            if self._other_entry_has_unique_id(entry, new_uid):
                return self.async_abort(reason="already_configured")

            errors = await _cloud_errors(
                self.hass,
                email=email,
                password=password,
                sm_id=sm_id,
                api_key=api_key or None,
                check_stream=True,
            )
            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=new_uid,
                    title=f"Solarmanager {sm_id}",
                    data_updates={
                        CONF_EMAIL: email,
                        CONF_PASSWORD: password,
                        CONF_SM_ID: sm_id,
                        CONF_API_KEY: api_key or None,
                    },
                )

        return self.async_show_form(
            step_id="reconfigure_cloud",
            data_schema=vol.Schema({
                vol.Optional(CONF_EMAIL, default=entry.data.get(CONF_EMAIL, "")): EMAIL_SELECTOR,
                vol.Optional(CONF_PASSWORD, default=""): PASSWORD_SELECTOR,
                vol.Optional(CONF_SM_ID, default=entry.data.get(CONF_SM_ID, "")): str,
                vol.Optional(CONF_API_KEY, default=""): PASSWORD_SELECTOR,
            }),
            errors=errors,
        )

    async def async_step_reconfigure_local(self, user_input=None) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            host = normalize_local_host(user_input[CONF_HOST])
            scheme = user_input[CONF_SCHEME]
            api_key = user_input.get(CONF_API_KEY) or entry.data.get(CONF_API_KEY)

            new_uid = f"local_{host}"
            if self._other_entry_has_unique_id(entry, new_uid):
                return self.async_abort(reason="already_configured")

            errors = await _local_errors(self.hass, host=host, scheme=scheme, api_key=api_key)
            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=new_uid,
                    title=f"Solarmanager Local ({host})",
                    data_updates={
                        CONF_HOST: host,
                        CONF_SCHEME: scheme,
                        CONF_API_KEY: api_key,
                    },
                )

        return self.async_show_form(
            step_id="reconfigure_local",
            data_schema=_schema_local(entry.data),
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        return SolarmanagerOptionsFlow()


class SolarmanagerOptionsFlow(config_entries.OptionsFlowWithReload):
    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        is_local = self.config_entry.data.get(CONF_MODE) == MODE_LOCAL
        errors: dict[str, str] = {}

        if user_input is not None:
            new_data: dict[str, Any] | None = None
            if not is_local:
                api_key = (user_input.get(CONF_API_KEY) or "").strip() or None
                if api_key:
                    current = self.config_entry.data
                    errors = await _cloud_errors(
                        self.hass,
                        email=current.get(CONF_EMAIL, ""),
                        password=current.get(CONF_PASSWORD, ""),
                        sm_id=current[CONF_SM_ID],
                        api_key=api_key,
                    )
                    if not errors:
                        new_data = {**current, CONF_API_KEY: api_key}

            if not errors:
                new_options = {
                    **self.config_entry.options,
                    CONF_SCAN_INTERVAL: user_input[CONF_SCAN_INTERVAL],
                }
                if new_data is not None:
                    data_changed = self.hass.config_entries.async_update_entry(
                        self.config_entry, data=new_data
                    )
                    if data_changed and new_options == dict(self.config_entry.options):
                        # OptionsFlowWithReload lädt nur bei geänderten Options
                        # neu — wenn nur der API Key (Data) geändert wurde,
                        # den Reload selbst planen.
                        self.hass.config_entries.async_schedule_reload(
                            self.config_entry.entry_id
                        )
                return self.async_create_entry(title="", data=new_options)

        if is_local:
            schema = vol.Schema({
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN),
                ): vol.All(vol.Coerce(int), vol.Range(min=10)),
            })
            return self.async_show_form(step_id="init", data_schema=schema, errors=errors)

        has_api_key = bool(self.config_entry.data.get(CONF_API_KEY))
        schema = vol.Schema({
            vol.Optional(CONF_API_KEY, default=""): PASSWORD_SELECTOR,
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN),
            ): vol.All(vol.Coerce(int), vol.Range(min=10)),
        })
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
            description_placeholders={"api_key_status": "gesetzt ✓" if has_api_key else "nicht gesetzt"},
        )
