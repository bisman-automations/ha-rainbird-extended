"""Tests for switches, start_zone, water, runs, schedule, programs and buttons."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rainbird_extended.config_flow import nest
from custom_components.rainbird_extended.const import (
    CONF_DISABLE_RAINBIRD_SWITCHES,
    CONF_STOP_ACTION,
    STOP_ACTION_STOP_IRRIGATION,
)
from homeassistant.components.number import (
    ATTR_VALUE,
    DOMAIN as NUMBER_DOMAIN,
    SERVICE_SET_VALUE,
)
from homeassistant.components.valve import DOMAIN as VALVE_DOMAIN, SERVICE_OPEN_VALVE
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .helpers import _advance, _open_options, _poll_core, _press, zone_calendar_events

VALVE = "valve.rain_bird_sprinkler_2"
SWITCH = "switch.rain_bird_sprinkler_2"
RUNTIME = "number.rain_bird_sprinkler_2_valve_runtime"
FLOW = "number.rain_bird_sprinkler_2_flow_rate"
WATER = "sensor.rain_bird_sprinkler_2_water_used"
LAST_RUN = "sensor.rain_bird_sprinkler_2_last_run"
NEXT_RUN = "sensor.rain_bird_sprinkler_2_estimated_next_run"
SEASONAL_A = "number.rain_bird_controller_seasonal_adjustment_a"
SEASONAL_SENSOR = "sensor.rain_bird_controller_seasonal_adjustment"
RUN_ALL = "button.rain_bird_controller_run_all_zones"
STOP = "button.rain_bird_controller_stop_irrigation"
PROGRAM_B = "button.rain_bird_controller_run_program_b"


@pytest.mark.parametrize("extended_options", [{CONF_DISABLE_RAINBIRD_SWITCHES: True}])
async def test_rainbird_switches_are_left_alone(
    hass: HomeAssistant,
    setup_integrations: MagicMock,
    extended_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """An old 'disable the Rain Bird switches' setting is dropped, not applied.

    The switches stay enabled, including one the user disabled themselves.
    Removing Rain Bird Extended does not re-enable anything.
    """
    entity_registry.async_update_entity(
        "switch.rain_bird_sprinkler_1", disabled_by=er.RegistryEntryDisabler.USER
    )
    switch = entity_registry.async_get(SWITCH)
    assert switch.disabled_by is None
    assert hass.states.get(SWITCH).state == "off"
    assert hass.states.get(VALVE).state == "closed"
    assert CONF_DISABLE_RAINBIRD_SWITCHES not in extended_entry.options

    result = await _open_options(hass, extended_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    submitted = nest({CONF_STOP_ACTION: STOP_ACTION_STOP_IRRIGATION})
    assert CONF_DISABLE_RAINBIRD_SWITCHES not in submitted
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], submitted
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert extended_entry.options[CONF_STOP_ACTION] == STOP_ACTION_STOP_IRRIGATION
    assert entity_registry.async_get(SWITCH).disabled_by is None
    assert (
        entity_registry.async_get("switch.rain_bird_sprinkler_1").disabled_by
        is er.RegistryEntryDisabler.USER
    )


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
    assert 1.8 < used < 2.4  # about a minute at 2 L/min

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
    """Zone 2 runs after zone 1 when the calendar lists those run times."""
    events = zone_calendar_events("Zone 1: 10 min\nZone 2: 15 min")

    async def fake(_core: object, _start: object, _end: object) -> list[dict[str, str]]:
        return events

    with patch(
        "custom_components.rainbird_extended.coordinator.RainbirdCore.async_calendar_events",
        fake,
    ):
        coordinator = hass.config_entries.async_entries("rainbird_extended")[
            0
        ].runtime_data
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    now = dt_util.now()
    expected = now.replace(hour=5, minute=10, second=0, microsecond=0)
    if expected <= now:
        expected += timedelta(days=1)
    assert dt_util.parse_datetime(hass.states.get(NEXT_RUN).state) == expected
    assert hass.states.get(NEXT_RUN).attributes["estimated"] is False
    # Zone 3 isn't listed: a day after it was first needed.
    state = hass.states.get("sensor.rain_bird_sprinkler_3_estimated_next_run")
    assert state.attributes["estimated"] is True
    estimate = dt_util.parse_datetime(state.state)
    assert timedelta(hours=23) < estimate - dt_util.utcnow() <= timedelta(days=1)


async def test_no_seasonal_adjustment_entity(
    hass: HomeAssistant, setup_integrations: MagicMock
) -> None:
    """Seasonal adjustment isn't created: Rain Bird has no public way to set it."""
    assert hass.states.get(SEASONAL_A) is None
    assert hass.states.get(SEASONAL_SENSOR) is None
    assert hass.states.get("number.rain_bird_controller_seasonal_adjustment_c") is None
    setup_integrations.get_combined_controller_state.assert_not_awaited()
    setup_integrations.water_budget.assert_not_awaited()
    setup_integrations.set_water_budget.assert_not_awaited()


async def test_run_program_and_stop(
    hass: HomeAssistant, setup_integrations: MagicMock
) -> None:
    """Program buttons start the program; stop stops everything."""
    if hass.states.get("button.rain_bird_controller_run_program_a") is None:
        # Home Assistant 2026.9 has no Run program button to press.
        await _press(hass, STOP)
        setup_integrations.stop_irrigation.assert_awaited_once()
        return
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


@pytest.mark.parametrize("extended_options", [{}])
async def test_stop_and_turn_off_needs_a_power_switch(
    hass: HomeAssistant,
    setup_integrations: MagicMock,
    extended_entry: MockConfigEntry,
) -> None:
    """Turning the controller off needs a switch or input boolean to turn off."""
    hass.states.async_set("switch.irrigation_controller", "on")
    hass.states.async_set("binary_sensor.controller_power", "on")
    for power, error in (
        (None, "power_switch_required"),
        ("binary_sensor.controller_power", "power_switch_required"),
        ("switch.irrigation_controller", None),
    ):
        result = await _open_options(hass, extended_entry.entry_id)
        values = {CONF_STOP_ACTION: "stop_and_turn_off"}
        if power:
            values["controller_power_entity"] = power
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], nest(values)
        )
        if error:
            assert result["errors"] == {CONF_STOP_ACTION: error}
            await hass.config_entries.options.async_configure(
                result["flow_id"], nest({CONF_STOP_ACTION: STOP_ACTION_STOP_IRRIGATION})
            )
        else:
            assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
    assert extended_entry.options[CONF_STOP_ACTION] == "stop_and_turn_off"
    assert extended_entry.options["controller_power_entity"] == (
        "switch.irrigation_controller"
    )
