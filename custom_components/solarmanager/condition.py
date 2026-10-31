# condition.py — PV-Überschuss und Einspeiselimit/Abregelung (siehe README für Details)
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Unpack

import voluptuous as vol
from homeassistant.const import CONF_FOR, CONF_OPTIONS, CONF_TARGET
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.condition import (
    Condition,
    ConditionCheckParams,
    ConditionConfig,
)
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .coordinator import (
    SolarmanagerCoordinator,
    compute_surplus_w,
    resolve_coordinator_for_device,
)
from .entity import local_day_bounds
from .forecast import Band
from .trigger import CONF_THRESHOLD, DEFAULT_FOR, SURPLUS_CONFIG_SCHEMA

_LOGGER = logging.getLogger(__name__)


class SurplusPresentCondition(Condition):
    """Wahr, wenn der PV-Überschuss seit mindestens `for` durchgehend über dem Threshold liegt.

    `None` (unknown), solange keine gültigen Leistungsdaten vorliegen — nicht mit
    `False` verwechseln, das bedeutet "Daten da, aber (noch) kein Überschuss".
    """

    def __init__(self, hass: HomeAssistant, config: ConditionConfig) -> None:
        super().__init__(hass, config)
        # Unconditional zuerst setzen: ConditionChecker.__del__ ruft beim Garbage
        # Collection immer async_unload() -> _async_unload() auf, auch wenn unten
        # eine HomeAssistantError-Exception das __init__ abbricht. Ohne diese
        # Attribute schon gesetzt zu haben, crasht das __del__ mit AttributeError.
        self._last_surplus: float | None = None
        self._since: datetime | None = None
        self._remove_listener: CALLBACK_TYPE | None = None

        device_ids = (config.target or {}).get("device_id") or []
        if not device_ids:
            raise HomeAssistantError(
                "solarmanager.is_surplus_present: target muss ein Solarmanager-Gerät sein"
            )
        self._device_id: str = device_ids[0]
        options = config.options or {}
        self._threshold: float = float(options[CONF_THRESHOLD])
        self._for: timedelta = options[CONF_FOR]

    @classmethod
    async def async_validate_config(cls, hass: HomeAssistant, config: ConfigType) -> ConfigType:
        return SURPLUS_CONFIG_SCHEMA(config)

    async def _async_setup(self) -> None:
        coord = resolve_coordinator_for_device(self._hass, self._device_id)

        @callback
        def _handle_update() -> None:
            self._last_surplus = compute_surplus_w(coord.data)
            above = self._last_surplus is not None and self._last_surplus > self._threshold
            if above:
                if self._since is None:
                    self._since = dt_util.utcnow()
            else:
                self._since = None

        _handle_update()
        self._remove_listener = coord.async_add_listener(_handle_update)

    def _async_unload(self) -> None:
        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None

    def _async_check(self, **kwargs: Unpack[ConditionCheckParams]) -> bool | None:
        if self._last_surplus is None:
            return None
        if self._since is None:
            return False
        return (dt_util.utcnow() - self._since) >= self._for


# -------------------- Abregelung: Anteil der Generatorleistung --------------------

CONF_SOURCE = "source"
CONF_PERCENT = "percent"
CONF_BAND = "band"
SOURCE_FEED_IN = "feed_in"
SOURCE_PRODUCTION = "production"
BAND_EXPECTED = "expected"
BAND_MAX = "max"
DEFAULT_PERCENT = 60.0

# Leistungsfeld im Stream je Quelle
_SOURCE_KEYS = {SOURCE_FEED_IN: "eW", SOURCE_PRODUCTION: "pW"}
# Prognose-Band je Auswahl (forecast.Band)
_BAND_KEYS: dict[str, Band] = {BAND_EXPECTED: "mid", BAND_MAX: "max"}

_PERCENT = vol.All(vol.Coerce(float), vol.Range(min=1, max=100))

GENERATOR_SHARE_CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TARGET): cv.TARGET_FIELDS,
        vol.Required(CONF_OPTIONS, default={}): vol.Schema(
            {
                vol.Required(CONF_SOURCE, default=SOURCE_FEED_IN): vol.In(list(_SOURCE_KEYS)),
                vol.Required(CONF_PERCENT, default=DEFAULT_PERCENT): _PERCENT,
                vol.Required(CONF_FOR, default=DEFAULT_FOR): cv.positive_time_period,
            }
        ),
    }
)

CURTAILMENT_EXPECTED_CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TARGET): cv.TARGET_FIELDS,
        vol.Required(CONF_OPTIONS, default={}): vol.Schema(
            {
                vol.Required(CONF_PERCENT, default=DEFAULT_PERCENT): _PERCENT,
                vol.Required(CONF_BAND, default=BAND_EXPECTED): vol.In(list(_BAND_KEYS)),
            }
        ),
    }
)


def _target_device_id(config: ConditionConfig, name: str) -> str:
    device_ids = (config.target or {}).get("device_id") or []
    if not device_ids:
        raise HomeAssistantError(
            f"solarmanager.{name}: target muss ein Solarmanager-Gerät sein"
        )
    return device_ids[0]


class GeneratorSharePowerCondition(Condition):
    """Wahr, wenn Einspeisung bzw. Produktion seit `for` mindestens `percent` %
    der Generatorleistung beträgt (Einspeiselimit erreicht, #23).

    Mit `source: production` bleibt die Bedingung wahr, nachdem ein Verbraucher
    zugeschaltet wurde — die Einspeisung sinkt dann, die Produktion nicht.
    `None`, solange Generatorleistung oder Leistungsdaten fehlen.
    """

    def __init__(self, hass: HomeAssistant, config: ConditionConfig) -> None:
        super().__init__(hass, config)
        # Vor einer möglichen Exception setzen, siehe SurplusPresentCondition
        self._last_value: float | None = None
        self._since: datetime | None = None
        self._remove_listener: CALLBACK_TYPE | None = None

        self._device_id = _target_device_id(config, "is_power_above_generator_share")
        options = config.options or {}
        self._value_key = _SOURCE_KEYS[options[CONF_SOURCE]]
        self._percent = float(options[CONF_PERCENT])
        self._for: timedelta = options[CONF_FOR]

    @classmethod
    async def async_validate_config(cls, hass: HomeAssistant, config: ConfigType) -> ConfigType:
        return GENERATOR_SHARE_CONFIG_SCHEMA(config)

    async def _async_setup(self) -> None:
        coord = resolve_coordinator_for_device(self._hass, self._device_id)

        @callback
        def _handle_update() -> None:
            threshold = coord.generator_share_w(self._percent)
            value = (coord.data or {}).get(self._value_key)
            if threshold is None or value is None:
                self._last_value = None
                self._since = None
                return
            self._last_value = float(value)
            if self._last_value >= threshold:
                if self._since is None:
                    self._since = dt_util.utcnow()
            else:
                self._since = None

        _handle_update()
        self._remove_listener = coord.async_add_listener(_handle_update)

    def _async_unload(self) -> None:
        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None

    def _async_check(self, **kwargs: Unpack[ConditionCheckParams]) -> bool | None:
        if self._last_value is None:
            return None
        if self._since is None:
            return False
        return (dt_util.utcnow() - self._since) >= self._for


class CurtailmentExpectedTodayCondition(Condition):
    """Wahr, wenn die PV-Prognose von jetzt bis Mitternacht `percent` % der
    Generatorleistung erreicht (Abregelung heute möglich, #23).

    Obere Abschätzung: verglichen wird die Produktionsprognose, ohne
    Hausverbrauch und Akkuladung. `None` ohne Prognose (lokaler Modus) oder
    ohne Generatorleistung.
    """

    def __init__(self, hass: HomeAssistant, config: ConditionConfig) -> None:
        super().__init__(hass, config)
        self._coord: SolarmanagerCoordinator | None = None
        self._device_id = _target_device_id(config, "is_curtailment_expected_today")
        options = config.options or {}
        self._percent = float(options[CONF_PERCENT])
        self._band: Band = _BAND_KEYS[options[CONF_BAND]]

    @classmethod
    async def async_validate_config(cls, hass: HomeAssistant, config: ConfigType) -> ConfigType:
        return CURTAILMENT_EXPECTED_CONFIG_SCHEMA(config)

    async def _async_setup(self) -> None:
        self._coord = resolve_coordinator_for_device(self._hass, self._device_id)

    def _async_check(self, **kwargs: Unpack[ConditionCheckParams]) -> bool | None:
        coord = self._coord
        if coord is None or coord.forecast is None:
            return None
        threshold = coord.generator_share_w(self._percent)
        if threshold is None:
            return None
        now = dt_util.utcnow()
        _, day_end = local_day_bounds(now)
        peak = coord.forecast.peak_between(now, day_end, self._band)
        return peak is not None and peak.value(self._band) >= threshold


CONDITIONS: dict[str, type[Condition]] = {
    "is_surplus_present": SurplusPresentCondition,
    "is_power_above_generator_share": GeneratorSharePowerCondition,
    "is_curtailment_expected_today": CurtailmentExpectedTodayCondition,
}


async def async_get_conditions(hass: HomeAssistant) -> dict[str, type[Condition]]:
    """Return the conditions provided by Solar Manager."""
    return CONDITIONS
