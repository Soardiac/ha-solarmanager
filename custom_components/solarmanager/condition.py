# condition.py — „PV-Überschuss vorhanden" (Spike, siehe README für Details)
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Unpack

from homeassistant.const import CONF_FOR
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.condition import (
    Condition,
    ConditionCheckParams,
    ConditionConfig,
)
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .coordinator import compute_surplus_w, resolve_coordinator_for_device
from .trigger import CONF_THRESHOLD, SURPLUS_OPTIONS_SCHEMA

_LOGGER = logging.getLogger(__name__)


class SurplusPresentCondition(Condition):
    """Wahr, wenn der PV-Überschuss seit mindestens `for` durchgehend über dem Threshold liegt.

    `None` (unknown), solange keine gültigen Leistungsdaten vorliegen — nicht mit
    `False` verwechseln, das bedeutet "Daten da, aber (noch) kein Überschuss".
    """

    def __init__(self, hass: HomeAssistant, config: ConditionConfig) -> None:
        super().__init__(hass, config)
        device_ids = (config.target or {}).get("device_id") or []
        if not device_ids:
            raise HomeAssistantError(
                "solarmanager.is_surplus_present: target muss ein Solarmanager-Gerät sein"
            )
        self._device_id: str = device_ids[0]
        options = config.options or {}
        self._threshold: float = float(options[CONF_THRESHOLD])
        self._for: timedelta = options[CONF_FOR]
        self._last_surplus: float | None = None
        self._since: datetime | None = None
        self._remove_listener: CALLBACK_TYPE | None = None

    @classmethod
    async def async_validate_config(cls, hass: HomeAssistant, config: ConfigType) -> ConfigType:
        return SURPLUS_OPTIONS_SCHEMA(config)

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


CONDITIONS: dict[str, type[Condition]] = {"is_surplus_present": SurplusPresentCondition}


async def async_get_conditions(hass: HomeAssistant) -> dict[str, type[Condition]]:
    """Return the conditions provided by Solar Manager."""
    return CONDITIONS
