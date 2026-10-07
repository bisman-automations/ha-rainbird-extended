"""Tests for run events and repairs."""

from __future__ import annotations

from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
from pyrainbird.exceptions import RainbirdDeviceNackError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rainbird_extended.const import CONF_RAINBIRD_ENTRY_ID, DOMAIN
from custom_components.rainbird_extended.repairs import async_create_fix_flow
from homeassistant.components.valve import DOMAIN as VALVE_DOMAIN, SERVICE_OPEN_VALVE
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from .helpers import _local, _poll_core, _setup, _unload

VALVE = "valve.rain_bird_sprinkler_2"
RUN_EVENT = "event.rain_bird_sprinkler_2_run"
LAST_RUN = "sensor.rain_bird_sprinkler_2_last_run"


async def test_run_events(
    hass: HomeAssistant,
    setup_integrations: MagicMock,
    active_zones: set[int],
    freezer: FrozenDateTimeFactory,
) -> None:
    """Started and finished events, with who started the run."""
    await hass.services.async_call(
        VALVE_DOMAIN, SERVICE_OPEN_VALVE, {ATTR_ENTITY_ID: VALVE}, blocking=True
    )
    await hass.async_block_till_done()
    state = hass.states.get(RUN_EVENT)
    assert state.attributes["event_type"] == "started"
    assert state.attributes["source"] == "home_assistant"
    assert hass.states.get(LAST_RUN).attributes["source"] == "home_assistant"

    for _ in range(2):
        await _poll_core(hass, freezer)
    active_zones.clear()
    await _poll_core(hass, freezer)
    state = hass.states.get(RUN_EVENT)
    assert state.attributes["event_type"] == "finished"
    assert state.attributes["duration"] > 120


async def test_run_from_outside_is_other(
    hass: HomeAssistant,
    setup_integrations: MagicMock,
    active_zones: set[int],
    freezer: FrozenDateTimeFactory,
) -> None:
    """A run Home Assistant didn't start and isn't scheduled now."""
    freezer.move_to(_local(14, 0))
    active_zones.add(2)
    await _poll_core(hass, freezer)
    assert hass.states.get(RUN_EVENT).attributes["source"] == "other"


async def test_scheduled_run_source(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    controller: MagicMock,
    mock_rainbird: MagicMock,
    rainbird_entry: MockConfigEntry,
    extended_entry: MockConfigEntry,
    active_zones: set[int],
) -> None:
    """A run during the zone's scheduled time is from the schedule."""
    freezer.move_to(_local(5, 11))
    controller.get_combined_controller_state.side_effect = RainbirdDeviceNackError()
    await _setup(hass, rainbird_entry, extended_entry)
    await hass.config_entries.async_entries("rainbird")[
        0
    ].runtime_data.schedule_coordinator.async_refresh()
    active_zones.add(2)
    await _poll_core(hass, freezer)
    assert hass.states.get(RUN_EVENT).attributes["source"] == "schedule"
    await _unload(hass, rainbird_entry, extended_entry)


async def test_repair_when_rainbird_removed(
    hass: HomeAssistant, issue_registry: ir.IssueRegistry
) -> None:
    """A missing Rain Bird entry raises a fixable issue that removes ours."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Rain Bird Extended",
        unique_id="gone",
        data={CONF_RAINBIRD_ENTRY_ID: "gone"},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    issue_id = f"rainbird_removed_{entry.entry_id}"
    issue = issue_registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.is_fixable

    flow = await async_create_fix_flow(hass, issue_id, issue.data)
    flow.hass = hass
    flow.issue_id = issue_id
    result = await flow.async_step_init()
    assert result["type"] is FlowResultType.FORM
    result = await flow.async_step_confirm({})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert hass.config_entries.async_get_entry(entry.entry_id) is None
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


async def test_repair_after_rainbird_deleted(
    hass: HomeAssistant,
    mock_rainbird: MagicMock,
    rainbird_entry: MockConfigEntry,
    extended_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Deleting Rain Bird while loaded reloads us into the repair issue."""
    await _setup(hass, rainbird_entry, extended_entry)
    issue_id = f"rainbird_removed_{extended_entry.entry_id}"
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None
    await hass.config_entries.async_remove(rainbird_entry.entry_id)
    await hass.async_block_till_done()
    assert extended_entry.state is ConfigEntryState.SETUP_ERROR
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is not None
    assert dt_util.utcnow()  # entry stays until the user removes it
