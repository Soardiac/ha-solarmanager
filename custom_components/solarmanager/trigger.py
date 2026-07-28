# trigger.py — „PV-Überschuss verfügbar" (Spike, siehe README für Details)
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

from .coordinator import compute_surplus_w, resolve_coordinator_for_device

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


class SurplusAvailableTrigger(Trigger):
    """Feuert, wenn der PV-Überschuss den Threshold für die konfigurierte Dauer überschreitet.

    Debounce (`for`, Default 2 min) verhindert, dass eine einzelne Wolkenlücke
    oder ein kurzer Anlaufstrom sofort auslöst — der Stream pollt alle ~10 s.
    """

    @classmethod
    async def async_validate_config(cls, hass: HomeAssistant, config: ConfigType) -> ConfigType:
        return SURPLUS_CONFIG_SCHEMA(config)

    def __init__(self, hass: HomeAssistant, config: TriggerConfig) -> None:
        super().__init__(hass, config)
        device_ids = (config.target or {}).get("device_id") or []
        if not device_ids:
            raise HomeAssistantError(
                "solarmanager.surplus_available: target muss ein Solarmanager-Gerät sein"
            )
        self._device_id: str = device_ids[0]
        options = config.options or {}
        self._threshold: float = float(options[CONF_THRESHOLD])
        self._for: timedelta = options[CONF_FOR]

    async def async_attach_runner(
        self,
        run_action: TriggerActionRunner,
        did_not_trigger: Any = None,
    ) -> CALLBACK_TYPE:
        coord = resolve_coordinator_for_device(self._hass, self._device_id)
        pending_cancel: CALLBACK_TYPE | None = None
        # Getrennt vom Pending-Timer: "sind wir seit der letzten Flanke über dem
        # Threshold" — bleibt True, auch nachdem der Timer schon gefeuert hat,
        # damit ein dauerhafter Überschuss nicht bei jedem Update neu auslöst.
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
            surplus = compute_surplus_w(coord.data)
            if surplus is None or surplus <= self._threshold:
                return
            run_action(
                {"surplus_w": surplus, "threshold": self._threshold},
                f"PV-Überschuss über {self._threshold} W für {self._for}",
            )

        @callback
        def _handle_update() -> None:
            nonlocal pending_cancel, active
            surplus = compute_surplus_w(coord.data)
            above = surplus is not None and surplus > self._threshold
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


TRIGGERS: dict[str, type[Trigger]] = {"surplus_available": SurplusAvailableTrigger}


async def async_get_triggers(hass: HomeAssistant) -> dict[str, type[Trigger]]:
    """Return the triggers provided by Solar Manager."""
    return TRIGGERS
