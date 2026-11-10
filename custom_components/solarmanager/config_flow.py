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
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api_client import (
    SolarmanagerApiError,
    SolarmanagerAuthError,
    SolarmanagerCloud,
    SolarmanagerLocal,
    normalize_local_host,
)
from .const import (
    CLOUD_BASE,
    CONF_API_KEY,
    CONF_CLOUD_API_KEY,
    CONF_CLOUD_EXTENSION,
    CONF_EMAIL,
    CONF_GENERATOR_KWP,
    CONF_HOST,
    CONF_LOCAL_API_KEY,
    CONF_MODE,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_SCHEME,
    CONF_SM_ID,
    DEFAULT_SCAN,
    DOMAIN,
    ENTRY_TITLE,
    MODE_CLOUD,
    MODE_LOCAL,
    PASSWORD_AUTH_DEADLINE,
)

_LOGGER = logging.getLogger(__name__)

PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
EMAIL_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL))
KWP_SELECTOR = NumberSelector(
    NumberSelectorConfig(
        min=0, max=1000, step=0.1, mode=NumberSelectorMode.BOX, unit_of_measurement="kWp"
    )
)


def _schema_cloud(defaults: dict | None = None) -> vol.Schema:
    """Cloud-Felder. Nur die smId ist Pflicht — als Auth genügt entweder der
    API Key oder E-Mail+Passwort (siehe _cloud_auth_error)."""
    defaults = defaults or {}
    return vol.Schema({
        vol.Required(CONF_SM_ID, default=defaults.get(CONF_SM_ID, "")): str,
        vol.Optional(CONF_API_KEY, default=""): PASSWORD_SELECTOR,
        vol.Optional(CONF_EMAIL, default=defaults.get(CONF_EMAIL, "")): EMAIL_SELECTOR,
        vol.Optional(CONF_PASSWORD, default=""): PASSWORD_SELECTOR,
    })


def _cloud_auth_error(user_input: Mapping[str, Any]) -> dict[str, str]:
    """Prüft, dass genau ein vollständiger Auth-Weg vorliegt (Key ODER Mail+Passwort)."""
    if user_input.get(CONF_API_KEY):
        return {}
    if user_input.get(CONF_EMAIL) and user_input.get(CONF_PASSWORD):
        return {}
    return {"base": "auth_method_required"}


def _cloud_entry_data(user_input: Mapping[str, Any]) -> dict[str, Any]:
    """Config-Entry-Daten aus dem Formular — leerer API Key wird zu None."""
    return {
        CONF_MODE: MODE_CLOUD,
        CONF_SM_ID: user_input[CONF_SM_ID],
        CONF_EMAIL: user_input.get(CONF_EMAIL, ""),
        CONF_PASSWORD: user_input.get(CONF_PASSWORD, ""),
        CONF_API_KEY: user_input.get(CONF_API_KEY) or None,
    }


def _remembered_local(data: Mapping[str, Any]) -> dict[str, Any]:
    """Lokale Verbindungsdaten — im Cloud-Modus die beim Wechsel gemerkten."""
    is_local = data.get(CONF_MODE) == MODE_LOCAL
    return {
        CONF_HOST: data.get(CONF_HOST, ""),
        CONF_SCHEME: data.get(CONF_SCHEME, "http"),
        CONF_API_KEY: data.get(CONF_API_KEY if is_local else CONF_LOCAL_API_KEY),
    }


def _remembered_cloud(data: Mapping[str, Any]) -> dict[str, Any]:
    """Cloud-Zugangsdaten — im lokalen Modus die der Cloud-Ergänzung bzw. gemerkten."""
    is_local = data.get(CONF_MODE) == MODE_LOCAL
    return {
        CONF_SM_ID: data.get(CONF_SM_ID, ""),
        CONF_API_KEY: data.get(CONF_CLOUD_API_KEY if is_local else CONF_API_KEY),
        CONF_EMAIL: data.get(CONF_EMAIL, ""),
        CONF_PASSWORD: data.get(CONF_PASSWORD, ""),
    }


def _schema_local(defaults: dict | None = None, *, with_extension: bool = False) -> vol.Schema:
    defaults = defaults or {}
    fields = {
        vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
        vol.Required(CONF_SCHEME, default=defaults.get(CONF_SCHEME, "http")): vol.In(["http", "https"]),
        vol.Optional(CONF_API_KEY, default=""): PASSWORD_SELECTOR,
    }
    if with_extension:
        fields[
            vol.Optional(CONF_CLOUD_EXTENSION, default=defaults.get(CONF_CLOUD_EXTENSION, False))
        ] = BooleanSelector()
    return vol.Schema(fields)


def _schema_cloud_extension(defaults: Mapping[str, Any] | None = None) -> vol.Schema:
    """Zugangsdaten der Cloud-Ergänzung: nur smId + API-Key (kein E-Mail/Passwort)."""
    defaults = defaults or {}
    return vol.Schema({
        vol.Required(CONF_SM_ID, default=defaults.get(CONF_SM_ID, "")): str,
        vol.Optional(CONF_CLOUD_API_KEY, default=""): PASSWORD_SELECTOR,
    })


async def _cloud_extension_errors(hass, *, sm_id: str, api_key: str | None) -> dict[str, str]:
    """Zugangsdaten der Cloud-Ergänzung prüfen — derselbe Weg wie im Cloud-Modus."""
    if not api_key:
        return {"base": "cloud_api_key_required"}
    return await _cloud_errors(hass, email="", password="", sm_id=sm_id, api_key=api_key)


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
    # 1.2: Site-Gerät mit stabilem Identifier site_{entry_id}
    # 1.3: Entry-Titel stabil „Solar Manager“ statt smId/Host
    MINOR_VERSION = 3

    # Lokale Daten zwischen den Schritten „local“/„switch_to_local“ und dem
    # jeweiligen Folgeschritt für die Cloud-Ergänzung
    _local_data: dict[str, Any] | None = None
    _switch_uid: str | None = None

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
            errors = _cloud_auth_error(user_input)
            if not errors:
                errors = await _cloud_errors(
                    self.hass,
                    email=user_input.get(CONF_EMAIL, ""),
                    password=user_input.get(CONF_PASSWORD, ""),
                    sm_id=user_input[CONF_SM_ID],
                    api_key=(user_input.get(CONF_API_KEY) or None),
                    check_stream=True,
                )
            if not errors:
                await self.async_set_unique_id(f"{DOMAIN}_{user_input[CONF_SM_ID]}")
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=ENTRY_TITLE,
                    data=_cloud_entry_data(user_input),
                )

        return self.async_show_form(
            step_id="cloud",
            data_schema=_schema_cloud(user_input),
            errors=errors,
            description_placeholders={"deadline": PASSWORD_AUTH_DEADLINE},
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
                data = {
                    CONF_MODE: MODE_LOCAL,
                    CONF_HOST: host,
                    CONF_SCHEME: scheme,
                    CONF_API_KEY: api_key,
                }
                if user_input.get(CONF_CLOUD_EXTENSION):
                    self._local_data = data
                    return await self.async_step_local_cloud()
                return self.async_create_entry(title=ENTRY_TITLE, data=data)

        return self.async_show_form(
            step_id="local",
            data_schema=_schema_local(user_input, with_extension=True),
            errors=errors,
        )

    async def async_step_local_cloud(self, user_input=None) -> ConfigFlowResult:
        """Zugangsdaten der Cloud-Ergänzung (nach erfolgreicher lokaler Prüfung)."""
        errors: dict[str, str] = {}

        if user_input is not None:
            sm_id = user_input[CONF_SM_ID].strip()
            api_key = (user_input.get(CONF_CLOUD_API_KEY) or "").strip() or None
            errors = await _cloud_extension_errors(self.hass, sm_id=sm_id, api_key=api_key)
            if not errors:
                return self.async_create_entry(
                    title=ENTRY_TITLE,
                    data={**(self._local_data or {}), CONF_SM_ID: sm_id, CONF_CLOUD_API_KEY: api_key},
                    options={CONF_CLOUD_EXTENSION: True},
                )

        return self.async_show_form(
            step_id="local_cloud",
            data_schema=_schema_cloud_extension(user_input),
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
        """Moduswechsel Lokal -> Cloud.

        Gemerkte Cloud-Zugangsdaten (Cloud-Ergänzung oder früherer Cloud-Modus)
        sind vorbelegt; leere Key-/Passwort-Felder übernehmen sie. Die lokalen
        Verbindungsdaten bleiben im Hintergrund gespeichert, damit ein
        Rückwechsel ohne Neueingabe geht.
        """
        entry = self._get_reconfigure_entry()
        remembered = _remembered_cloud(entry.data)
        errors: dict[str, str] = {}

        if user_input is not None:
            new_uid = f"{DOMAIN}_{user_input[CONF_SM_ID]}"
            if self._other_entry_has_unique_id(entry, new_uid):
                return self.async_abort(reason="already_configured")

            user_input = {
                **user_input,
                CONF_API_KEY: user_input.get(CONF_API_KEY) or remembered[CONF_API_KEY],
                CONF_PASSWORD: user_input.get(CONF_PASSWORD) or remembered[CONF_PASSWORD],
            }
            errors = _cloud_auth_error(user_input)
            if not errors:
                errors = await _cloud_errors(
                    self.hass,
                    email=user_input.get(CONF_EMAIL, ""),
                    password=user_input.get(CONF_PASSWORD, ""),
                    sm_id=user_input[CONF_SM_ID],
                    api_key=(user_input.get(CONF_API_KEY) or None),
                    check_stream=True,
                )
            if not errors:
                local = _remembered_local(entry.data)
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=new_uid,
                    data={
                        **_cloud_entry_data(user_input),
                        CONF_HOST: local[CONF_HOST],
                        CONF_SCHEME: local[CONF_SCHEME],
                        CONF_LOCAL_API_KEY: local[CONF_API_KEY],
                    },
                )

        return self.async_show_form(
            step_id="switch_to_cloud",
            data_schema=_schema_cloud(user_input or remembered),
            errors=errors,
            description_placeholders={"deadline": PASSWORD_AUTH_DEADLINE},
        )

    async def async_step_switch_to_local(self, user_input=None) -> ConfigFlowResult:
        """Moduswechsel Cloud -> Lokal, auf Wunsch gleich mit Cloud-Ergänzung.

        Gemerkte lokale Daten sind vorbelegt; ein leeres Key-Feld übernimmt den
        gemerkten lokalen Key. Die Cloud-Zugangsdaten bleiben gespeichert: Mit
        API-Key reicht dieser eine Schritt für die Cloud-Ergänzung, sonst folgt
        die Eingabe des Keys.
        """
        entry = self._get_reconfigure_entry()
        remembered = _remembered_local(entry.data)
        errors: dict[str, str] = {}

        if user_input is not None:
            host = normalize_local_host(user_input[CONF_HOST])
            scheme = user_input[CONF_SCHEME]
            api_key = user_input.get(CONF_API_KEY) or remembered[CONF_API_KEY] or None

            new_uid = f"local_{host}"
            if self._other_entry_has_unique_id(entry, new_uid):
                return self.async_abort(reason="already_configured")

            errors = await _local_errors(self.hass, host=host, scheme=scheme, api_key=api_key)
            if not errors:
                cloud = _remembered_cloud(entry.data)
                data = {
                    CONF_MODE: MODE_LOCAL,
                    CONF_HOST: host,
                    CONF_SCHEME: scheme,
                    CONF_API_KEY: api_key,
                    # Cloud-Zugangsdaten gemerkt (Cloud-Ergänzung, Rückwechsel)
                    CONF_SM_ID: cloud[CONF_SM_ID],
                    CONF_CLOUD_API_KEY: cloud[CONF_API_KEY],
                    CONF_EMAIL: cloud[CONF_EMAIL],
                    CONF_PASSWORD: cloud[CONF_PASSWORD],
                }
                options = {
                    k: v for k, v in entry.options.items() if k != CONF_CLOUD_EXTENSION
                }
                if not user_input.get(CONF_CLOUD_EXTENSION):
                    return self.async_update_reload_and_abort(
                        entry, unique_id=new_uid, data=data, options=options
                    )
                if cloud[CONF_SM_ID] and cloud[CONF_API_KEY]:
                    # Der Key lief eben noch im Cloud-Modus — keine erneute Prüfung
                    return self.async_update_reload_and_abort(
                        entry,
                        unique_id=new_uid,
                        data=data,
                        options={**options, CONF_CLOUD_EXTENSION: True},
                    )
                # Cloud-Modus lief mit E-Mail/Passwort: API-Key nachfragen
                self._local_data = data
                self._switch_uid = new_uid
                return await self.async_step_switch_to_local_cloud()

        return self.async_show_form(
            step_id="switch_to_local",
            data_schema=_schema_local(
                {**remembered, CONF_CLOUD_EXTENSION: True}, with_extension=True
            ),
            errors=errors,
        )

    async def async_step_switch_to_local_cloud(self, user_input=None) -> ConfigFlowResult:
        """Moduswechsel auf Lokal mit Cloud-Ergänzung, aber ohne gemerkten API-Key."""
        entry = self._get_reconfigure_entry()
        data = self._local_data or {}
        errors: dict[str, str] = {}

        if user_input is not None:
            sm_id = user_input[CONF_SM_ID].strip()
            api_key = (user_input.get(CONF_CLOUD_API_KEY) or "").strip() or None
            errors = await _cloud_extension_errors(self.hass, sm_id=sm_id, api_key=api_key)
            if not errors:
                options = {
                    k: v for k, v in entry.options.items() if k != CONF_CLOUD_EXTENSION
                }
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=self._switch_uid,
                    data={**data, CONF_SM_ID: sm_id, CONF_CLOUD_API_KEY: api_key},
                    options={**options, CONF_CLOUD_EXTENSION: True},
                )

        return self.async_show_form(
            step_id="switch_to_local_cloud",
            data_schema=_schema_cloud_extension(user_input or data),
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
    # Optionen aus „init“, solange der Schritt „cloud“ (Cloud-Ergänzung) noch aussteht
    _pending_options: dict[str, Any] | None = None

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
                # Leer oder 0 → Schlüssel entfernen, dann gilt der Wert aus Solar Manager
                kwp = user_input.get(CONF_GENERATOR_KWP)
                if kwp:
                    new_options[CONF_GENERATOR_KWP] = float(kwp)
                else:
                    new_options.pop(CONF_GENERATOR_KWP, None)
                if is_local:
                    if user_input.get(CONF_CLOUD_EXTENSION):
                        # Zugangsdaten im nächsten Schritt bestätigen bzw. erfassen
                        self._pending_options = new_options
                        return await self.async_step_cloud()
                    # Ausschalten: keine Cloud-Aufrufe mehr. Die Zugangsdaten
                    # bleiben gemerkt, damit das Wiedereinschalten ohne
                    # Neueingabe geht (sie werden ohne den Schalter nie benutzt).
                    new_options.pop(CONF_CLOUD_EXTENSION, None)
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

        common = {
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN),
            ): vol.All(vol.Coerce(int), vol.Range(min=10)),
            # suggested_value statt default: das Feld lässt sich wieder leeren
            vol.Optional(
                CONF_GENERATOR_KWP,
                description={"suggested_value": self.config_entry.options.get(CONF_GENERATOR_KWP)},
            ): KWP_SELECTOR,
        }
        # Wert aus Solar Manager als Orientierung anzeigen (nur Cloud, nur wenn geladen)
        coord = getattr(self.config_entry, "runtime_data", None)
        api_kwp = getattr(coord, "api_kwp", None)
        placeholders = {
            "api_kwp": f"{api_kwp:g} kWp" if api_kwp else "nicht verfügbar",
        }

        if is_local:
            schema = vol.Schema({
                **common,
                vol.Optional(
                    CONF_CLOUD_EXTENSION,
                    default=bool(self.config_entry.options.get(CONF_CLOUD_EXTENSION)),
                ): BooleanSelector(),
            })
            return self.async_show_form(
                step_id="init",
                data_schema=schema,
                errors=errors,
                description_placeholders={**placeholders, "api_key_status": "–"},
            )

        has_api_key = bool(self.config_entry.data.get(CONF_API_KEY))
        schema = vol.Schema({
            vol.Optional(CONF_API_KEY, default=""): PASSWORD_SELECTOR,
            **common,
        })
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                **placeholders,
                "api_key_status": "gesetzt ✓" if has_api_key else "nicht gesetzt",
            },
        )

    async def async_step_cloud(self, user_input=None) -> ConfigFlowResult:
        """Cloud-Ergänzung (lokaler Modus): smId und API-Key erfassen bzw. bestätigen.

        Ein leeres Key-Feld behält den gespeicherten Key. Geprüft wird nur, wenn
        sich smId oder Key geändert haben — so kostet das reine Bestätigen beim
        Ändern anderer Optionen keinen Cloud-Aufruf.
        """
        current = self.config_entry.data
        errors: dict[str, str] = {}

        if user_input is not None:
            sm_id = user_input[CONF_SM_ID].strip()
            api_key = (user_input.get(CONF_CLOUD_API_KEY) or "").strip() or current.get(
                CONF_CLOUD_API_KEY
            )
            unchanged = sm_id == current.get(CONF_SM_ID) and api_key == current.get(
                CONF_CLOUD_API_KEY
            )
            if not unchanged:
                errors = await _cloud_extension_errors(self.hass, sm_id=sm_id, api_key=api_key)
            if not errors:
                new_options = {
                    **(self._pending_options or dict(self.config_entry.options)),
                    CONF_CLOUD_EXTENSION: True,
                }
                data_changed = self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data={**current, CONF_SM_ID: sm_id, CONF_CLOUD_API_KEY: api_key},
                )
                if data_changed and new_options == dict(self.config_entry.options):
                    # Nur Zugangsdaten geändert — OptionsFlowWithReload lädt dann nicht neu
                    self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
                return self.async_create_entry(title="", data=new_options)

        return self.async_show_form(
            step_id="cloud",
            data_schema=_schema_cloud_extension(user_input or current),
            errors=errors,
            description_placeholders={
                "key_status": "gespeichert ✓" if current.get(CONF_CLOUD_API_KEY) else "nicht gesetzt",
            },
        )
