"""Repairs for Rain Bird Extended."""

from __future__ import annotations

from typing import Any

from homeassistant.components.repairs import (
    ConfirmRepairFlow,
    RepairsFlow,
    RepairsFlowResult,
)
from homeassistant.core import HomeAssistant


class RemoveEntryRepairFlow(ConfirmRepairFlow):
    """Remove a Rain Bird Extended entry whose Rain Bird controller is gone."""

    def __init__(self, entry_id: str) -> None:
        """Initialize the flow."""
        super().__init__()
        self._entry_id = entry_id

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Remove the entry once confirmed."""
        if user_input is not None:
            if self.hass.config_entries.async_get_entry(self._entry_id):
                await self.hass.config_entries.async_remove(self._entry_id)
            return self.async_create_entry(data={})
        return self.async_show_form(step_id="confirm")


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    """Create the fix flow for an issue."""
    return RemoveEntryRepairFlow(str((data or {}).get("entry_id", "")))
