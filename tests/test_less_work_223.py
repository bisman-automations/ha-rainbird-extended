"""2.2.2: less work on each state write, and run history kept over a reload."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock, patch

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rainbird_extended import calendar as run_calendar
from homeassistant.components.valve import (
    DOMAIN as VALVE_DOMAIN,
    SERVICE_CLOSE_VALVE,
    SERVICE_OPEN_VALVE,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .helpers import _advance, _setup, _unload

CALENDAR = "calendar.rain_bird_controller_run_history"
VALVE = "valve.rain_bird_sprinkler_2"


def _history(count: int) -> list[dict[str, Any]]:
    """A long run history, oldest first, one run a day on zone 1."""
    now = dt_util.utcnow().replace(microsecond=0)
    runs = []
    for day in range(count, 0, -1):
        start = now - timedelta(days=day)
        runs.append(
            {
                "zone": 1,
                "start": start.isoformat(),
                "end": (start + timedelta(minutes=5)).isoformat(),
                "source": "schedule",
            }
        )
    return runs


async def test_run_history_state_reads_only_the_newest_run(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    mock_rainbird: MagicMock,
    rainbird_entry: MockConfigEntry,
    extended_entry: MockConfigEntry,
) -> None:
    """Writing the calendar's state doesn't go through the whole history."""
    runs = _history(300)
    hass_storage[f"rainbird_extended.{extended_entry.entry_id}.run_history"] = {
        "version": 1,
        "key": f"rainbird_extended.{extended_entry.entry_id}.run_history",
        "data": {"runs": runs},
    }
    await _setup(hass, rainbird_entry, extended_entry)
    entity = hass.data["entity_components"]["calendar"].get_entity(CALENDAR)
    state = hass.states.get(CALENDAR)
    assert state.attributes["start_time"] == dt_util.as_local(
        dt_util.parse_datetime(runs[-1]["start"])
    ).strftime("%Y-%m-%d %H:%M:%S")

    built: list[int] = []
    real = run_calendar.RainbirdRunHistory._event

    def counted(self: Any, *args: Any) -> Any:
        built.append(1)
        return real(self, *args)

    with patch.object(run_calendar.RainbirdRunHistory, "_event", counted):
        entity.async_write_ha_state()
    # The state reads the current event a few times: each time one stored
    # run is looked at, not all 300 (900 events built before 2.2.2).
    assert 0 < len(built) <= 5
    await _unload(hass, rainbird_entry, extended_entry)


async def test_run_kept_when_the_entry_reloads(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    setup_integrations: MagicMock,
    extended_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A run that just finished is saved before a reload, not lost."""
    await hass.services.async_call(
        VALVE_DOMAIN, SERVICE_OPEN_VALVE, {ATTR_ENTITY_ID: VALVE}, blocking=True
    )
    await _advance(hass, freezer, timedelta(minutes=2))
    await hass.services.async_call(
        VALVE_DOMAIN, SERVICE_CLOSE_VALVE, {ATTR_ENTITY_ID: VALVE}, blocking=True
    )
    await hass.async_block_till_done()
    key = f"rainbird_extended.{extended_entry.entry_id}.run_history"
    # Waiting for its delayed save.
    assert key not in hass_storage

    assert await hass.config_entries.async_reload(extended_entry.entry_id)
    await hass.async_block_till_done()
    assert [run["zone"] for run in hass_storage[key]["data"]["runs"]] == [2]
    state = hass.states.get(CALENDAR)
    assert state.attributes["description"] == "2 min, started by Home Assistant"


async def test_zone_entities_read_the_registry_once_per_change(
    hass: HomeAssistant, setup_integrations: MagicMock
) -> None:
    """Writing every entity's state reads Rain Bird's registry entries once."""
    coordinator = hass.config_entries.async_entries("rainbird_extended")[0].runtime_data
    real = er.async_entries_for_config_entry
    reads: list[str] = []

    def counted(registry: er.EntityRegistry, entry_id: str) -> list[er.RegistryEntry]:
        if entry_id == coordinator.rainbird_entry.entry_id:
            reads.append(entry_id)
        return real(registry, entry_id)

    with patch.object(er, "async_entries_for_config_entry", counted):
        coordinator.async_update_listeners()
        await hass.async_block_till_done()
        # At most once, not several times for every entity (over 50 before).
        assert len(reads) <= 1

        # A registry change is picked up straight away.
        zone_switch = coordinator.core.zone_switches()[1]
        renamed = "switch.renamed_zone_1"
        er.async_get(hass).async_update_entity(zone_switch, new_entity_id=renamed)
        await hass.async_block_till_done()
        assert coordinator.core.zone_switches()[1] == renamed
