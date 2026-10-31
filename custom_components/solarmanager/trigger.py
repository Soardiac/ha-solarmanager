# trigger.py — „PV-Überschuss verfügbar" und „Anteil der Generatorleistung erreicht"
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import voluptuous as vol
from homeassistant.const import CONF_FOR, CONF_OPTIONS, CONF_TARGET
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.trigger import Trigger, TriggerActionRunner, TriggerConfig
from homeassistant.helpers.typing import ConfigType

from .coordinator import (
    SolarmanagerCoordinator,
    compute_surplus_w,
    resolve_coordinator_for_device,
)

_LOGGER = logging.getLogger(__name__)

CONF_THRESHOLD = "threshold"
DEFAULT_FOR = timedelta(minutes=2)

SURPLUS_OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_THRESHOLD, default=0): vol.Coerce(float),
        vol.Required(CONF_FOR, default=DEFAULT_FOR): cv.positive_time_period,
    }
)

# async_validate_complete_config() (homeassistant.helpers.trigger) reicht {target, options}
# als EIN kombiniertes Dict durch — nicht die flachen Options-Felder direkt. Gemeinsames
# Schema für Trigger und Condition (condition.py importiert es), Muster übernommen von
# ENTITY_STATE_CONDITION_SCHEMA_ANY_ALL in homeassistant/helpers/condition.py.
SURPLUS_CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TARGET): cv.TARGET_FIELDS,
        vol.Required(CONF_OPTIONS, default={}): SURPLUS_OPTIONS_SCHEMA,
    }
)

# -------------------- Anteil der Generatorleistung (Einspeiselimit, #23) --------------------
# Gemeinsam für Trigger und Condition (condition.py importiert von hier).

CONF_SOURCE = "source"
CONF_PERCENT = "percent"
SOURCE_FEED_IN = "feed_in"
SOURCE_PRODUCTION = "production"
DEFAULT_PERCENT = 60.0

# Leistungsfeld im Stream je Quelle
SOURCE_KEYS = {SOURCE_FEED_IN: "eW", SOURCE_PRODUCTION: "pW"}

PERCENT_VALIDATOR = vol.All(vol.Coerce(float), vol.Range(min=1, max=100))

GENERATOR_SHARE_CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TARGET): cv.TARGET_FIELDS,
        vol.Required(CONF_OPTIONS, default={}): vol.Schema(
            {
                vol.Required(CONF_SOURCE, default=SOURCE_FEED_IN): vol.In(list(SOURCE_KEYS)),
                vol.Required(CONF_PERCENT, default=DEFAULT_PERCENT): PERCENT_VALIDATOR,
                vol.Required(CONF_FOR, default=DEFAULT_FOR): cv.positive_time_period,
            }
        ),
    }
)


def generator_share_power(
    coord: SolarmanagerCoordinator, source: str, percent: float
) -> tuple[float | None, float | None]:
    """(aktuelle Leistung, Schwelle) in W für Einspeisung/Produktion vs. Generatoranteil.

    Je None, wenn Leistungsdaten bzw. Generatorleistung fehlen.
    """
    value = (coord.data or {}).get(SOURCE_KEYS[source])
    return (
        None if value is None else float(value),
        coord.generator_share_w(percent),
    )


def target_device_id(config: TriggerConfig | Any, name: str) -> str:
    """Erste device_id aus dem Target; HomeAssistantError, wenn keine gewählt ist."""
    device_ids = (config.target or {}).get("device_id") or []
    if not device_ids:
        raise HomeAssistantError(
            f"solarmanager.{name}: target muss ein Solarmanager-Gerät sein"
        )
    return device_ids[0]


# -------------------- Trigger --------------------

class _AboveForTrigger(Trigger):
    """Feuert einmal, wenn ein Messwert die Schwelle für die Dauer `for` überschreitet.

    Debounce (`for`) verhindert, dass eine einzelne Wolkenlücke oder ein kurzer
    Anlaufstrom sofort auslöst — der Stream pollt alle ~10 s. Erneut auslösen
    kann der Trigger erst, nachdem der Wert wieder unter die Schwelle gefallen ist.
    """

    _name: str

    def __init__(self, hass: HomeAssistant, config: TriggerConfig) -> None:
        super().__init__(hass, config)
        self._device_id: str = target_device_id(config, self._name)
        self._for: timedelta = (config.options or {})[CONF_FOR]

    def _measure(self, coord: SolarmanagerCoordinator) -> tuple[float | None, float | None]:
        """(Wert, Schwelle); None bei fehlenden Daten."""
        raise NotImplementedError

    def _is_above(self, value: float, threshold: float) -> bool:
        raise NotImplementedError

    def _payload(self, value: float, threshold: float) -> tuple[dict[str, Any], str]:
        """(Trigger-Variablen, Beschreibung) beim Auslösen."""
        raise NotImplementedError

    def _state(self, coord: SolarmanagerCoordinator) -> tuple[bool, float | None, float | None]:
        value, threshold = self._measure(coord)
        above = value is not None and threshold is not None and self._is_above(value, threshold)
        return above, value, threshold

    async def async_attach_runner(
        self,
        run_action: TriggerActionRunner,
        did_not_trigger: Any = None,
    ) -> CALLBACK_TYPE:
        coord = resolve_coordinator_for_device(self._hass, self._device_id)
        pending_cancel: CALLBACK_TYPE | None = None
        # Getrennt vom Pending-Timer: "sind wir seit der letzten Flanke über dem
        # Threshold" — bleibt True, auch nachdem der Timer schon gefeuert hat,
        # damit ein dauerhafter Zustand nicht bei jedem Update neu auslöst.
        active = False

        @callback
        def _clear_pending() -> None:
            nonlocal pending_cancel
            if pending_cancel is not None:
                pending_cancel()
                pending_cancel = None

        @callback
        def _fire(_now: Any) -> None:
            nonlocal pending_cancel
            pending_cancel = None
            above, value, threshold = self._state(coord)
            if not above:
                return
            variables, description = self._payload(value, threshold)
            run_action(variables, description)

        @callback
        def _handle_update() -> None:
            nonlocal pending_cancel, active
            above, _, _ = self._state(coord)
            if above:
                if not active:
                    active = True
                    pending_cancel = async_call_later(self._hass, self._for, _fire)
            else:
                active = False
                _clear_pending()

        remove_listener = coord.async_add_listener(_handle_update)

        @callback
        def _unsub() -> None:
            _clear_pending()
            remove_listener()

        return _unsub


class SurplusAvailableTrigger(_AboveForTrigger):
    """Feuert, wenn der PV-Überschuss den Threshold für die konfigurierte Dauer überschreitet."""

    _name = "surplus_available"

    @classmethod
    async def async_validate_config(cls, hass: HomeAssistant, config: ConfigType) -> ConfigType:
        return SURPLUS_CONFIG_SCHEMA(config)

    def __init__(self, hass: HomeAssistant, config: TriggerConfig) -> None:
        super().__init__(hass, config)
        self._threshold: float = float((config.options or {})[CONF_THRESHOLD])

    def _measure(self, coord: SolarmanagerCoordinator) -> tuple[float | None, float | None]:
        return compute_surplus_w(coord.data), self._threshold

    def _is_above(self, value: float, threshold: float) -> bool:
        return value > threshold

    def _payload(self, value: float, threshold: float) -> tuple[dict[str, Any], str]:
        return (
            {"surplus_w": value, "threshold": threshold},
            f"PV-Überschuss über {threshold} W für {self._for}",
        )


class GeneratorShareReachedTrigger(_AboveForTrigger):
    """Feuert, wenn Einspeisung bzw. Produktion für `for` mindestens `percent` %
    der Generatorleistung erreicht (Einspeiselimit erreicht, #23).

    Ohne Generatorleistung löst er nie aus.
    """

    _name = "generator_share_reached"

    @classmethod
    async def async_validate_config(cls, hass: HomeAssistant, config: ConfigType) -> ConfigType:
        return GENERATOR_SHARE_CONFIG_SCHEMA(config)

    def __init__(self, hass: HomeAssistant, config: TriggerConfig) -> None:
        super().__init__(hass, config)
        options = config.options or {}
        self._source: str = options[CONF_SOURCE]
        self._percent = float(options[CONF_PERCENT])

    def _measure(self, coord: SolarmanagerCoordinator) -> tuple[float | None, float | None]:
        return generator_share_power(coord, self._source, self._percent)

    def _is_above(self, value: float, threshold: float) -> bool:
        return value >= threshold

    def _payload(self, value: float, threshold: float) -> tuple[dict[str, Any], str]:
        return (
            {
                "source": self._source,
                "power_w": value,
                "threshold_w": threshold,
                "percent": self._percent,
            },
            f"{self._source} ≥ {self._percent:g} % der Generatorleistung "
            f"({threshold:.0f} W) für {self._for}",
        )


TRIGGERS: dict[str, type[Trigger]] = {
    "surplus_available": SurplusAvailableTrigger,
    "generator_share_reached": GeneratorShareReachedTrigger,
}


async def async_get_triggers(hass: HomeAssistant) -> dict[str, type[Trigger]]:
    """Return the triggers provided by Solar Manager."""
    return TRIGGERS
