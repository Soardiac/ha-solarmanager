# repairs.py — Reparatur-Flow für die E-Mail/Passwort-Abschaltung
from __future__ import annotations

import voluptuous as vol

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN


class DeprecatedPasswordAuthRepairFlow(RepairsFlow):
    """Führt den Nutzer in den bestehenden Reauth-Dialog, um einen API-Key zu hinterlegen.

    Dupliziert bewusst KEINE API-Key-Eingabe — die gesamte Key-Logik bleibt im
    Reauth-Flow (config_flow.async_step_reauth_confirm).
    """

    def __init__(self, entry_id: str | None) -> None:
        self._entry_id = entry_id

    async def async_step_init(self, user_input: dict | None = None):
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict | None = None):
        if user_input is not None:
            if self._entry_id is not None:
                entry = self.hass.config_entries.async_get_entry(self._entry_id)
                if entry is not None:
                    entry.async_start_reauth(self.hass)
            return self.async_create_entry(data={})

        # Frist/smId aus den Issue-Platzhaltern in den Dialog übernehmen
        issue_registry = ir.async_get(self.hass)
        placeholders = None
        if issue := issue_registry.async_get_issue(DOMAIN, self.issue_id):
            placeholders = issue.translation_placeholders

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders=placeholders,
        )


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Reparatur-Flow für ein fixbares Issue erzeugen."""
    entry_id = (data or {}).get("entry_id")
    return DeprecatedPasswordAuthRepairFlow(str(entry_id) if entry_id else None)
