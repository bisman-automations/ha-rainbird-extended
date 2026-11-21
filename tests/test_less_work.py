"""2.2.1: only Rain Bird's own entities wake the coordinator.

It used to look at every state change in Home Assistant and, for each one,
work out Rain Bird's entities again from the entity registry.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er


async def test_unrelated_state_changes_do_no_work(
    hass: HomeAssistant, setup_integrations: MagicMock
) -> None:
    coordinator = hass.config_entries.async_entries("rainbird_extended")[0].runtime_data
    zone_switch = coordinator.core.zone_switches()[1]
    lookups: list[int] = []
    real = coordinator.core.watched_entity_ids

    def counted() -> set[str]:
        lookups.append(1)
        return real()

    with (
        patch.object(coordinator.core, "watched_entity_ids", counted),
        patch.object(coordinator, "async_request_refresh", AsyncMock()) as refresh,
    ):
        coordinator._watched = None
        for number in range(100):
            hass.states.async_set(f"sensor.somewhere_else_{number}", str(number))
        await hass.async_block_till_done()
        refresh.assert_not_called()
        # Worked out once, not once per state change (100 before 2.2.1).
        assert len(lookups) == 1

        # A Rain Bird zone switch still refreshes, without a new lookup.
        hass.states.async_set(zone_switch, "on")
        await hass.async_block_till_done()
        assert refresh.await_count == 1
        assert len(lookups) == 1

        # Renaming it in the registry is picked up.
        renamed = "switch.renamed_zone_1"
        er.async_get(hass).async_update_entity(zone_switch, new_entity_id=renamed)
        await hass.async_block_till_done()
        refresh.reset_mock()
        hass.states.async_set(renamed, "off")
        await hass.async_block_till_done()
        assert refresh.await_count == 1
        assert renamed in coordinator.watched_entity_ids()
        assert zone_switch not in coordinator.watched_entity_ids()
