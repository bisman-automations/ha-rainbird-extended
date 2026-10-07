"""Tests for cycle and soak, Run all zones options, run events and repairs."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
from pyrainbird.exceptions import RainbirdDeviceNackError
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.rainbird_extended.const import (
    CONF_CYCLE_MINUTES,
    CONF_DISABLE_RAINBIRD_SWITCHES,
    CONF_RAINBIRD_ENTRY_ID,
    CONF_RUN_ALL_ZONES,
    CONF_SOAK_MINUTES,
    DOMAIN,
)
from custom_components.rainbird_extended.repairs import async_create_fix_flow
from custom_components.rainbird_extended.sequence import Step, plan_steps
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.components.valve import (
    DOMAIN as VALVE_DOMAIN,
    SERVICE_CLOSE_VALVE,
    SERVICE_OPEN_VALVE,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from .test_entities import _poll_core
from .test_v1_2 import _local, _setup, _unload

VALVE = "valve.rain_bird_sprinkler_2"
RUN_EVENT = "event.rain_bird_sprinkler_2_run"
LAST_RUN = "sensor.rain_bird_sprinkler_2_last_run"
MIN = 60


def _calls(controller: MagicMock) -> list[tuple[int, int]]:
    return [call.args for call in controller.irrigate_zone.await_args_list]


async def _advance(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta: timedelta
) -> None:
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def test_plan_without_cycles() -> None:
    """No cycle length: each zone once, in order; zero runtimes skipped."""
    assert plan_steps([(3, 300), (1, 0), (2, 600)]) == [Step(3, 300), Step(2, 600)]


def test_plan_cycle_and_soak_zones_take_turns() -> None:
    """Three 10 minute zones in 4 minute cycles with 10 minutes of soak."""
    assert plan_steps(
        [(1, 10 * MIN), (2, 10 * MIN), (3, 10 * MIN)], 4 * MIN, 10 * MIN
    ) == [
        Step(1, 4 * MIN),
        Step(2, 4 * MIN),
        Step(3, 4 * MIN),
        # Each zone has had 8 minutes since its cycle: wait 2 more.
        Step(None, 2 * MIN),
        Step(1, 4 * MIN),
        Step(2, 4 * MIN),
        Step(3, 4 * MIN),
        # Zone 3 would only rest 4 minutes before its short last cycle.
        Step(None, 6 * MIN),
        Step(1, 2 * MIN),
        Step(2, 2 * MIN),
        Step(3, 2 * MIN),
    ]


def test_plan_single_zone() -> None:
    """One zone waits the full soak between its cycles."""
    assert plan_steps([(2, 15 * MIN)], 5 * MIN, 10 * MIN) == [
        Step(2, 5 * MIN),
        Step(None, 10 * MIN),
        Step(2, 5 * MIN),
        Step(None, 10 * MIN),
        Step(2, 5 * MIN),
    ]


def test_plan_no_wait_when_others_cover_the_soak() -> None:
    """With enough zones, the others' cycles are the soak."""
    steps = plan_steps([(1, 8 * MIN), (2, 8 * MIN), (3, 8 * MIN)], 4 * MIN, 8 * MIN)
    assert Step(None, 0) not in steps
    assert all(step.zone is not None for step in steps)


@pytest.mark.parametrize(
    "extended_options",
    [{CONF_DISABLE_RAINBIRD_SWITCHES: False, CONF_RUN_ALL_ZONES: [3, 1]}],
)
async def test_run_all_zones_order(
    hass: HomeAssistant,
    setup_integrations: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Only the configured zones, in the configured order."""
    await hass.services.async_call(
        BUTTON_DOMAIN,
        SERVICE_PRESS,
        {ATTR_ENTITY_ID: "button.rain_bird_controller_run_all_zones"},
        blocking=True,
    )
    await _advance(hass, freezer, timedelta(minutes=6))
    await _advance(hass, freezer, timedelta(minutes=6))
    assert _calls(setup_integrations) == [(3, 6), (1, 6)]


@pytest.mark.parametrize(
    "extended_options",
    [
        {
            CONF_DISABLE_RAINBIRD_SWITCHES: False,
            CONF_CYCLE_MINUTES: 5,
            CONF_SOAK_MINUTES: 10,
        }
    ],
)
async def test_start_zone_cycle_and_soak(
    hass: HomeAssistant,
    setup_integrations: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """15 minutes as three 5 minute cycles with 10 minute soaks."""
    controller = setup_integrations
    await hass.services.async_call(
        DOMAIN,
        "start_zone",
        {ATTR_ENTITY_ID: VALVE, "duration": "00:15:00", "cycle_and_soak": True},
        blocking=True,
    )
    assert _calls(controller) == [(2, 5)]
    await _advance(hass, freezer, timedelta(minutes=5))
    assert _calls(controller) == [(2, 5)]  # soaking
    await _advance(hass, freezer, timedelta(minutes=10))
    assert _calls(controller) == [(2, 5), (2, 5)]
    await _advance(hass, freezer, timedelta(minutes=5))
    await _advance(hass, freezer, timedelta(minutes=10))
    assert _calls(controller) == [(2, 5), (2, 5), (2, 5)]
    await _advance(hass, freezer, timedelta(minutes=30))
    assert controller.irrigate_zone.await_count == 3

    # Without cycle_and_soak it's a single run.
    await hass.services.async_call(
        DOMAIN,
        "start_zone",
        {ATTR_ENTITY_ID: VALVE, "duration": "00:15:00"},
        blocking=True,
    )
    assert _calls(controller)[-1] == (2, 15)


@pytest.mark.parametrize(
    "extended_options",
    [
        {
            CONF_DISABLE_RAINBIRD_SWITCHES: False,
            CONF_CYCLE_MINUTES: 5,
            CONF_SOAK_MINUTES: 10,
        }
    ],
)
async def test_cycle_and_soak_stopped_while_soaking(
    hass: HomeAssistant,
    setup_integrations: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Closing the valve during a soak cancels the remaining cycles."""
    await hass.services.async_call(
        DOMAIN,
        "start_zone",
        {ATTR_ENTITY_ID: VALVE, "duration": "00:15:00", "cycle_and_soak": True},
        blocking=True,
    )
    await _advance(hass, freezer, timedelta(minutes=6))
    await hass.services.async_call(
        VALVE_DOMAIN, SERVICE_CLOSE_VALVE, {ATTR_ENTITY_ID: VALVE}, blocking=True
    )
    await _advance(hass, freezer, timedelta(minutes=30))
    assert setup_integrations.irrigate_zone.await_count == 1


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


@pytest.mark.usefixtures("setup_integrations")
async def test_options_zone_order(
    hass: HomeAssistant, extended_entry: MockConfigEntry
) -> None:
    """Zones are picked by their valves in order, and stored as zone numbers."""
    zone = "valve.rain_bird_sprinkler_{}".format
    result = await hass.config_entries.options.async_init(extended_entry.entry_id)
    picker = result["data_schema"].schema[CONF_RUN_ALL_ZONES].config
    assert picker["multiple"] and picker["reorder"]
    assert picker["include_entities"] == [zone(1), zone(2), zone(3)]

    # The picker only accepts this controller's zone valves.
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_DISABLE_RAINBIRD_SWITCHES: False,
                CONF_RUN_ALL_ZONES: [zone(3), "valve.someone_else"],
            },
        )

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_DISABLE_RAINBIRD_SWITCHES: False, CONF_RUN_ALL_ZONES: [zone(3), zone(1)]},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert extended_entry.options[CONF_RUN_ALL_ZONES] == [3, 1]
    assert extended_entry.options[CONF_CYCLE_MINUTES] == 0

    # Opening the options again shows the saved order as valves.
    result = await hass.config_entries.options.async_init(extended_entry.entry_id)
    key = next(k for k in result["data_schema"].schema if k == CONF_RUN_ALL_ZONES)
    assert key.description["suggested_value"] == [zone(3), zone(1)]


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
