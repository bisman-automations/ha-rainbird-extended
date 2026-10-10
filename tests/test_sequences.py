"""Tests for cycle and soak and Run all zones zones and order."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rainbird_extended.config_flow import nest
from custom_components.rainbird_extended.const import (
    CONF_CYCLE_MINUTES,
    CONF_DISABLE_RAINBIRD_SWITCHES,
    CONF_RUN_ALL_ZONES,
    CONF_SOAK_MINUTES,
    DOMAIN,
    SECTION_RUN_ALL_ZONES,
)
from custom_components.rainbird_extended.sequence import Step, plan_steps
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.components.valve import DOMAIN as VALVE_DOMAIN, SERVICE_CLOSE_VALVE
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.exceptions import HomeAssistantError

from .helpers import _advance, _calls, _open_options, _press

VALVE = "valve.rain_bird_sprinkler_2"
MIN = 60


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


@pytest.mark.usefixtures("setup_integrations")
async def test_options_zone_order(
    hass: HomeAssistant, extended_entry: MockConfigEntry
) -> None:
    """Zones are picked by their valves in order, and stored as zone numbers."""
    zone = "valve.rain_bird_sprinkler_{}".format
    result = await _open_options(hass, extended_entry.entry_id)
    section = result["data_schema"].schema[SECTION_RUN_ALL_ZONES]
    picker = section.schema.schema[CONF_RUN_ALL_ZONES].config
    assert picker["multiple"] and picker["reorder"]
    assert picker["include_entities"] == [zone(1), zone(2), zone(3)]

    # The picker only accepts this controller's zone valves.
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            nest(
                {
                    CONF_DISABLE_RAINBIRD_SWITCHES: False,
                    CONF_RUN_ALL_ZONES: [zone(3), "valve.someone_else"],
                }
            ),
        )

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        nest(
            {
                CONF_DISABLE_RAINBIRD_SWITCHES: False,
                CONF_RUN_ALL_ZONES: [zone(3), zone(1)],
            }
        ),
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert extended_entry.options[CONF_RUN_ALL_ZONES] == [3, 1]
    assert extended_entry.options[CONF_CYCLE_MINUTES] == 0

    # Opening the options again shows the saved order as valves.
    result = await _open_options(hass, extended_entry.entry_id)
    fields = result["data_schema"].schema[SECTION_RUN_ALL_ZONES].schema.schema
    key = next(k for k in fields if k == CONF_RUN_ALL_ZONES)
    assert key.description["suggested_value"] == [zone(3), zone(1)]


PAUSE = "button.rain_bird_controller_pause"
RESUME = "button.rain_bird_controller_resume"
IRRIGATING = "binary_sensor.rain_bird_controller_irrigating"


async def test_pause_and_resume_run_all_zones(
    hass: HomeAssistant,
    setup_integrations: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Pausing stops the zone and keeps what's left; resume picks it up."""
    controller = setup_integrations
    assert hass.states.get(PAUSE).state == "unavailable"
    assert hass.states.get(RESUME).state == "unavailable"
    await _press(hass, "button.rain_bird_controller_run_all_zones")
    assert _calls(controller) == [(1, 6)]
    assert hass.states.get(PAUSE).state != "unavailable"

    # 2 minutes 30 seconds into zone 1's 6 minutes.
    await _advance(hass, freezer, timedelta(minutes=2, seconds=30))
    await _press(hass, PAUSE)
    controller.stop_irrigation.assert_awaited_once()
    assert hass.states.get(IRRIGATING).attributes["paused"] == "run_all_zones"
    assert hass.states.get(PAUSE).state == "unavailable"
    assert hass.states.get(RESUME).state != "unavailable"

    # Nothing starts while paused.
    await _advance(hass, freezer, timedelta(minutes=30))
    assert controller.irrigate_zone.await_count == 1

    # 3.5 minutes left, rounded up to 4; then zones 2 and 3 as planned.
    await _press(hass, RESUME)
    assert _calls(controller)[-1] == (1, 4)
    assert hass.states.get(IRRIGATING).attributes["paused"] is None
    await _advance(hass, freezer, timedelta(minutes=4))
    assert _calls(controller)[-1] == (2, 6)
    await _advance(hass, freezer, timedelta(minutes=6))
    assert _calls(controller)[-1] == (3, 6)


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
async def test_pause_during_soak_and_stop_clears(
    hass: HomeAssistant,
    setup_integrations: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Paused mid-soak, the rest of the soak comes first; Stop forgets it."""
    controller = setup_integrations
    await hass.services.async_call(
        DOMAIN,
        "start_zone",
        {ATTR_ENTITY_ID: VALVE, "duration": "00:10:00", "cycle_and_soak": True},
        blocking=True,
    )
    await _advance(hass, freezer, timedelta(minutes=5))  # first cycle done
    await _advance(hass, freezer, timedelta(minutes=4))  # 6 of 10 soak left
    await _press(hass, PAUSE)
    await _press(hass, RESUME)
    assert _calls(controller) == [(2, 5)]  # still soaking
    await _advance(hass, freezer, timedelta(minutes=6))
    assert _calls(controller) == [(2, 5), (2, 5)]

    await _press(hass, PAUSE)
    await _press(hass, "button.rain_bird_controller_stop_irrigation")
    assert hass.states.get(RESUME).state == "unavailable"
    with pytest.raises(HomeAssistantError, match="Nothing is paused"):
        await hass.config_entries.async_entries(DOMAIN)[0].runtime_data.async_resume()
