"""Tests for switches, start_zone, water, runs, schedule, programs and buttons."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
from pyrainbird.data import WaterBudget
from pyrainbird.exceptions import RainbirdDeviceNackError
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.rainbird_extended.config_flow import nest
from custom_components.rainbird_extended.const import CONF_DISABLE_RAINBIRD_SWITCHES
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.components.number import (
    ATTR_VALUE,
    DOMAIN as NUMBER_DOMAIN,
    SERVICE_SET_VALUE,
)
from homeassistant.components.valve import DOMAIN as VALVE_DOMAIN, SERVICE_OPEN_VALVE
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .conftest import controller_state
from .test_entities import _poll_core

VALVE = "valve.rain_bird_sprinkler_2"
SWITCH = "switch.rain_bird_sprinkler_2"
RUNTIME = "number.rain_bird_sprinkler_2_valve_runtime"
FLOW = "number.rain_bird_sprinkler_2_flow_rate"
WATER = "sensor.rain_bird_sprinkler_2_water_used"
LAST_RUN = "sensor.rain_bird_sprinkler_2_last_run"
NEXT_RUN = "sensor.rain_bird_sprinkler_2_next_run"
SEASONAL_A = "number.rain_bird_controller_seasonal_adjustment_a"
SEASONAL_SENSOR = "sensor.rain_bird_controller_seasonal_adjustment"
RUN_ALL = "button.rain_bird_controller_run_all_zones"
STOP = "button.rain_bird_controller_stop_irrigation"
PROGRAM_B = "button.rain_bird_controller_run_program_b"


async def _press(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )
    await hass.async_block_till_done()


async def _advance(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta: timedelta
) -> None:
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


@pytest.mark.parametrize("extended_options", [{}])
async def test_switches_disabled_by_default(
    hass: HomeAssistant,
    setup_integrations: MagicMock,
    extended_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Valves replace the core switches, which are disabled until opted out."""
    switch = entity_registry.async_get(SWITCH)
    assert switch.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get(SWITCH) is None
    assert hass.states.get(VALVE).state == "closed"

    result = await hass.config_entries.options.async_init(extended_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], nest({CONF_DISABLE_RAINBIRD_SWITCHES: False})
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entity_registry.async_get(SWITCH).disabled_by is None


@pytest.mark.parametrize("extended_options", [{}])
async def test_switches_restored_on_removal(
    hass: HomeAssistant,
    mock_rainbird: MagicMock,
    rainbird_entry: MockConfigEntry,
    extended_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Removing Rain Bird Extended re-enables the switches it disabled only."""
    assert await hass.config_entries.async_setup(rainbird_entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(extended_entry.entry_id)
    await hass.async_block_till_done()
    assert entity_registry.async_get(SWITCH).disabled_by is not None
    entity_registry.async_update_entity(
        "switch.rain_bird_sprinkler_1", disabled_by=er.RegistryEntryDisabler.USER
    )
    await hass.config_entries.async_remove(extended_entry.entry_id)
    await hass.async_block_till_done()
    assert entity_registry.async_get(SWITCH).disabled_by is None
    assert (
        entity_registry.async_get("switch.rain_bird_sprinkler_1").disabled_by
        is er.RegistryEntryDisabler.USER
    )
    await hass.config_entries.async_unload(rainbird_entry.entry_id)


async def test_start_zone_action(
    hass: HomeAssistant, setup_integrations: MagicMock
) -> None:
    """start_zone runs once for the given time without changing the runtime."""
    start = dt_util.utcnow()
    await hass.services.async_call(
        "rainbird_extended",
        "start_zone",
        {ATTR_ENTITY_ID: VALVE, "duration": {"minutes": 20}},
        blocking=True,
    )
    await hass.async_block_till_done()
    setup_integrations.irrigate_zone.assert_awaited_once_with(2, 20)
    assert hass.states.get(VALVE).state == "open"
    assert hass.states.get(RUNTIME).state == "360"
    end = dt_util.parse_datetime(
        hass.states.get("sensor.rain_bird_sprinkler_2_time_remaining").state
    )
    assert abs(end - (start + timedelta(minutes=20))) < timedelta(seconds=1)


async def test_water_used_and_last_run(
    hass: HomeAssistant,
    setup_integrations: MagicMock,
    active_zones: set[int],
    freezer: FrozenDateTimeFactory,
) -> None:
    """A 6 minute run at 2 L/min uses 12 L and is recorded as the last run."""
    assert hass.states.get(WATER).state == "0.0"
    assert hass.states.get(WATER).attributes["unit_of_measurement"] == "L"
    assert hass.states.get(LAST_RUN).state == STATE_UNKNOWN
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: FLOW, ATTR_VALUE: 2},
        blocking=True,
    )

    start = dt_util.utcnow().replace(microsecond=0)
    await hass.services.async_call(
        VALVE_DOMAIN, SERVICE_OPEN_VALVE, {ATTR_ENTITY_ID: VALVE}, blocking=True
    )
    await hass.async_block_till_done()
    assert dt_util.parse_datetime(hass.states.get(LAST_RUN).state) == start
    assert hass.states.get(LAST_RUN).attributes["duration"] is None

    await _poll_core(hass, freezer)
    used = float(hass.states.get(WATER).state)
    assert 2.0 < used < 2.2  # just over a minute

    for _ in range(4):
        await _poll_core(hass, freezer)
    active_zones.clear()  # the controller finishes the run at 6 minutes
    await _poll_core(hass, freezer)

    # Counted up to the run's end, not until the controller was next asked.
    assert float(hass.states.get(WATER).state) == pytest.approx(12.0)
    last_run = hass.states.get(LAST_RUN)
    assert dt_util.parse_datetime(last_run.state) == start
    assert last_run.attributes["duration"] == 360

    # Idle: no more water.
    await _poll_core(hass, freezer)
    assert float(hass.states.get(WATER).state) == pytest.approx(12.0)


async def test_next_run(hass: HomeAssistant, setup_integrations: MagicMock) -> None:
    """Zone 2 runs after zone 1 in program A, so next at 05:10 local."""
    await hass.async_block_till_done()
    now = dt_util.now()
    expected = now.replace(hour=5, minute=10, second=0, microsecond=0)
    if expected <= now:
        expected += timedelta(days=1)
    assert dt_util.parse_datetime(hass.states.get(NEXT_RUN).state) == expected
    # Zone 3 isn't in any program.
    assert hass.states.get("sensor.rain_bird_sprinkler_3_next_run").state == (
        STATE_UNKNOWN
    )


async def test_seasonal_adjustment(
    hass: HomeAssistant,
    setup_integrations: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """One adjustable seasonal adjustment per program, re-read every 30 min."""
    controller = setup_integrations
    state = hass.states.get(SEASONAL_A)
    assert state.state == "100"
    assert state.attributes["unit_of_measurement"] == "%"
    assert state.attributes["min"] == 10
    assert state.attributes["max"] == 200
    assert hass.states.get("number.rain_bird_controller_seasonal_adjustment_c")
    assert hass.states.get(SEASONAL_SENSOR) is None
    # Idle: the controller state isn't asked for, only the water budgets.
    controller.get_combined_controller_state.assert_not_awaited()

    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: SEASONAL_A, ATTR_VALUE: 80},
        blocking=True,
    )
    controller.set_water_budget.assert_awaited_once_with(0, 80)
    assert hass.states.get(SEASONAL_A).state == "80"

    # Changed in the Rain Bird app: picked up on the next 30 minute read.
    controller.water_budget.side_effect = lambda key: WaterBudget(key, 120)
    await _poll_core(hass, freezer)
    assert hass.states.get(SEASONAL_A).state == "80"
    await _advance(hass, freezer, timedelta(minutes=30))
    await _poll_core(hass, freezer)
    assert hass.states.get(SEASONAL_A).state == "120"


async def test_seasonal_adjustment_read_only(
    hass: HomeAssistant,
    controller: MagicMock,
    mock_rainbird: MagicMock,
    rainbird_entry: MockConfigEntry,
    extended_entry: MockConfigEntry,
) -> None:
    """With pyrainbird 6.5 (no set_water_budget) it's shown read-only."""
    del controller.set_water_budget
    assert await hass.config_entries.async_setup(rainbird_entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(extended_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(SEASONAL_A) is None
    state = hass.states.get("sensor.rain_bird_controller_seasonal_adjustment_a")
    assert state.state == "100"
    assert state.attributes["unit_of_measurement"] == "%"
    await hass.config_entries.async_unload(extended_entry.entry_id)
    await hass.config_entries.async_unload(rainbird_entry.entry_id)


async def test_seasonal_adjustment_without_water_budget(
    hass: HomeAssistant,
    controller: MagicMock,
    mock_rainbird: MagicMock,
    rainbird_entry: MockConfigEntry,
    extended_entry: MockConfigEntry,
) -> None:
    """Controllers that reject water budgets fall back to a read-only sensor."""
    controller.water_budget.side_effect = RainbirdDeviceNackError()
    controller.get_combined_controller_state.return_value = controller_state(
        seasonal=90
    )
    assert await hass.config_entries.async_setup(rainbird_entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(extended_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(SEASONAL_A) is None
    assert hass.states.get(SEASONAL_SENSOR).state == "90"
    await hass.config_entries.async_unload(extended_entry.entry_id)
    await hass.config_entries.async_unload(rainbird_entry.entry_id)


@pytest.mark.parametrize("active_zones", [{2}])
async def test_seasonal_adjustment_unsupported(
    hass: HomeAssistant,
    controller: MagicMock,
    mock_rainbird: MagicMock,
    rainbird_entry: MockConfigEntry,
    extended_entry: MockConfigEntry,
) -> None:
    """Controllers that report neither show the sensor as unavailable."""
    controller.water_budget.side_effect = RainbirdDeviceNackError()
    controller.get_combined_controller_state.side_effect = RainbirdDeviceNackError()
    assert await hass.config_entries.async_setup(rainbird_entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(extended_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(SEASONAL_SENSOR).state == STATE_UNAVAILABLE
    await hass.config_entries.async_unload(extended_entry.entry_id)
    await hass.config_entries.async_unload(rainbird_entry.entry_id)


async def test_run_program_and_stop(
    hass: HomeAssistant, setup_integrations: MagicMock
) -> None:
    """Program buttons start the program; stop stops everything."""
    assert hass.states.get("button.rain_bird_controller_run_program_a")
    assert hass.states.get("button.rain_bird_controller_run_program_c")
    assert hass.states.get("button.rain_bird_controller_run_program_d") is None
    await _press(hass, PROGRAM_B)
    setup_integrations.set_program.assert_awaited_once_with(1)
    await _press(hass, STOP)
    setup_integrations.stop_irrigation.assert_awaited_once()


async def test_run_all_zones(
    hass: HomeAssistant,
    setup_integrations: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Each zone runs for its runtime, one after another."""
    controller = setup_integrations
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: "number.rain_bird_sprinkler_1_valve_runtime", ATTR_VALUE: 120},
        blocking=True,
    )
    await _press(hass, RUN_ALL)
    assert controller.irrigate_zone.await_args_list[-1].args == (1, 2)

    await _advance(hass, freezer, timedelta(minutes=2))
    assert controller.irrigate_zone.await_args_list[-1].args == (2, 6)
    assert hass.states.get(VALVE).state == "open"

    await _advance(hass, freezer, timedelta(minutes=6))
    assert controller.irrigate_zone.await_args_list[-1].args == (3, 6)

    await _advance(hass, freezer, timedelta(minutes=6))
    assert controller.irrigate_zone.await_count == 3
    entry = hass.config_entries.async_entries("rainbird_extended")[0]
    assert not entry.runtime_data.sequence_running


async def test_run_all_zones_stopped(
    hass: HomeAssistant,
    setup_integrations: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Stopping from Home Assistant ends the sequence."""
    await _press(hass, RUN_ALL)
    await _press(hass, STOP)
    await _advance(hass, freezer, timedelta(minutes=10))
    assert setup_integrations.irrigate_zone.await_count == 1


async def test_run_all_zones_stopped_elsewhere(
    hass: HomeAssistant,
    setup_integrations: MagicMock,
    active_zones: set[int],
    freezer: FrozenDateTimeFactory,
) -> None:
    """A zone stopped early from outside Home Assistant ends the sequence."""
    await _press(hass, RUN_ALL)
    await _poll_core(hass, freezer)  # zone 1 confirmed running
    active_zones.clear()  # stopped from the Rain Bird app
    await _poll_core(hass, freezer)
    await _advance(hass, freezer, timedelta(minutes=10))
    assert setup_integrations.irrigate_zone.await_count == 1
