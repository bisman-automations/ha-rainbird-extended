"""Tests for programs set up in the options (controllers that can't share theirs)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
from pyrainbird.exceptions import RainbirdDeviceNackError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rainbird_extended.config_flow import nest
from homeassistant.components.calendar import DOMAIN as CALENDAR_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util

from .helpers import _local, _open_options, _poll_core, _press, _setup, _unload

SCHEDULE = "calendar.rain_bird_controller_schedule"

PROGRAM_A = {
    "name": "Normal Watering",
    "frequency": "cyclic",
    "every_days": 2,
    "start_date": "2026-10-11",
    "start_time_1": "07:00:00",
    "zones": [1, 2],
    "station_delay": 30,
    "seasonal_adjust": 100,
}


def _next(hass: HomeAssistant, zone: int) -> datetime | None:
    state = hass.states.get(f"sensor.rain_bird_sprinkler_{zone}_estimated_next_run")
    return dt_util.parse_datetime(state.state)


def _remaining(hass: HomeAssistant, zone: int) -> datetime | None:
    state = hass.states.get(f"sensor.rain_bird_sprinkler_{zone}_time_remaining")
    return dt_util.parse_datetime(state.state)


async def _events(
    hass: HomeAssistant, start: datetime, end: datetime
) -> list[dict[str, Any]]:
    response = await hass.services.async_call(
        CALENDAR_DOMAIN,
        "get_events",
        {ATTR_ENTITY_ID: SCHEDULE, "start_date_time": start, "end_date_time": end},
        blocking=True,
        return_response=True,
    )
    return response[SCHEDULE]["events"]


@pytest.mark.freeze_time("2026-10-10 17:00:00+00:00")
async def test_program_options(
    hass: HomeAssistant, setup_integrations: MagicMock, extended_entry: MockConfigEntry
) -> None:
    """Each program has its own form; the settings form keeps them."""
    result = await hass.config_entries.options.async_init(extended_entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == [
        "settings",
        "program_a",
        "program_b",
        "program_c",
    ]
    result = await _open_options(hass, extended_entry.entry_id, "program_a")
    assert result["step_id"] == "program_a"

    # A Custom program needs a day.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "frequency": "custom",
            "days": [],
            "every_days": 2,
            "start_time_1": "07:00:00",
            "zones": ["valve.rain_bird_sprinkler_2"],
            "station_delay": 0,
            "seasonal_adjust": 100,
        },
    )
    assert result["errors"] == {"days": "no_days"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "frequency": "custom",
            "days": ["mon", "wed"],
            "every_days": 2,
            "start_time_1": "07:00:00",
            "zones": ["valve.rain_bird_sprinkler_2", "valve.rain_bird_sprinkler_1"],
            "station_delay": 0,
            "seasonal_adjust": 100,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert extended_entry.options["program_a"]["zones"] == [1, 2]
    assert extended_entry.options["disable_rainbird_switches"] is False
    # The schedule calendar comes with the program (Monday is next).
    assert hass.states.get(SCHEDULE).attributes["start_time"] == "2026-10-12 07:00:00"

    # Saving the settings leaves the program alone.
    result = await _open_options(hass, extended_entry.entry_id)
    assert result["step_id"] == "settings"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], nest({"disable_rainbird_switches": True})
    )
    await hass.async_block_till_done()
    assert extended_entry.options["program_a"]["days"] == ["mon", "wed"]

    # Opening the program again shows its zones as valves.
    result = await _open_options(hass, extended_entry.entry_id, "program_a")
    key = next(k for k in result["data_schema"].schema if k == "zones")
    assert key.description["suggested_value"] == [
        "valve.rain_bird_sprinkler_1",
        "valve.rain_bird_sprinkler_2",
    ]


@pytest.mark.parametrize(
    "extended_options",
    [{"disable_rainbird_switches": False, "program_a": PROGRAM_A}],
)
async def test_cyclic_program(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_rainbird: MagicMock,
    rainbird_entry: MockConfigEntry,
    extended_entry: MockConfigEntry,
) -> None:
    """Every other day from the start date, zones in turn with the delay."""
    freezer.move_to(_local(12, 0))  # Oct 10, the day before the start date
    await _setup(hass, rainbird_entry, extended_entry)
    tomorrow = _local(7, 0) + timedelta(days=1)
    assert _next(hass, 1) == dt_util.as_utc(tomorrow)
    # Zone 1 runs 6 minutes, then 30 seconds' delay.
    assert _next(hass, 2) == dt_util.as_utc(tomorrow + timedelta(minutes=6, seconds=30))
    state = hass.states.get("sensor.rain_bird_sprinkler_2_estimated_next_run")
    assert state.attributes["estimated"] is False
    # Zone 3 isn't in a program: estimated from its interval.
    state = hass.states.get("sensor.rain_bird_sprinkler_3_estimated_next_run")
    assert state.attributes["estimated"] is True

    state = hass.states.get(SCHEDULE)
    assert state.state == "off"
    assert state.attributes["message"] == "Program A: Normal Watering"
    events = await _events(hass, _local(0, 0), _local(0, 0) + timedelta(days=6))
    assert [dt_util.parse_datetime(e["start"]) for e in events] == [
        tomorrow + timedelta(days=days) for days in (0, 2, 4)
    ]
    assert dt_util.parse_datetime(events[0]["end"]) == tomorrow + timedelta(
        minutes=12, seconds=30
    )
    assert events[0]["description"] == "Rain Bird Sprinkler 1, Rain Bird Sprinkler 2"
    await _unload(hass, rainbird_entry, extended_entry)


@pytest.mark.parametrize(
    ("frequency", "day"), [("odd", 11), ("even", 12), ("custom", 13)]
)
async def test_frequencies(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_rainbird: MagicMock,
    rainbird_entry: MockConfigEntry,
    frequency: str,
    day: int,
) -> None:
    """Odd and even days of the month, and days of the week."""
    entry = MockConfigEntry(
        domain="rainbird_extended",
        unique_id=rainbird_entry.entry_id,
        data={"rainbird_entry_id": rainbird_entry.entry_id},
        options={
            "program_b": {
                "frequency": frequency,
                "days": ["tue"],
                "start_time_1": "06:00:00",
                "start_time_2": "19:30:00",
                "zones": [3],
            }
        },
    )
    entry.add_to_hass(hass)
    freezer.move_to(_local(20, 0))  # Saturday Oct 10, after both start times
    await _setup(hass, rainbird_entry, entry)
    assert _next(hass, 3) == dt_util.as_utc(_local(6, 0) + timedelta(days=day - 10))
    assert hass.states.get(SCHEDULE).attributes["message"] == "Program B"
    await _unload(hass, rainbird_entry, entry)


@pytest.mark.parametrize(
    "extended_options",
    [
        {
            "disable_rainbird_switches": False,
            "program_a": {**PROGRAM_A, "start_date": "2026-10-01"},
        }
    ],
)
@pytest.mark.parametrize(
    ("budget", "now", "end"),
    [(100, (7, 8), (7, 12, 30)), (50, (7, 4), (7, 6, 30))],
)
async def test_time_remaining_from_program(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    controller: MagicMock,
    mock_rainbird: MagicMock,
    rainbird_entry: MockConfigEntry,
    extended_entry: MockConfigEntry,
    active_zones: set[int],
    budget: int,
    now: tuple[int, int],
    end: tuple[int, ...],
) -> None:
    """A scheduled run ends when the program says, scaled by seasonal adjust."""
    hass.config_entries.async_update_entry(
        extended_entry,
        options={
            **extended_entry.options,
            "program_a": {**PROGRAM_A, "seasonal_adjust": budget},
        },
    )
    # Oct 11 is a run day (every other day from Oct 1). The controller doesn't
    # report its seasonal adjust or what's left of a run.
    freezer.move_to(_local(*now) + timedelta(days=1))
    active_zones.add(2)
    controller.get_combined_controller_state.side_effect = RainbirdDeviceNackError()
    controller.water_budget.side_effect = RainbirdDeviceNackError()
    await _setup(hass, rainbird_entry, extended_entry)
    await _poll_core(hass, freezer)
    assert _remaining(hass, 2) == dt_util.as_utc(_local(*end))
    state = hass.states.get("sensor.rain_bird_sprinkler_2_last_run")
    assert state.attributes["source"] == "schedule"
    await _unload(hass, rainbird_entry, extended_entry)


async def test_rain_delay_skips_runs(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    controller: MagicMock,
    mock_rainbird: MagicMock,
    rainbird_entry: MockConfigEntry,
) -> None:
    """Days under the controller's rain delay are left out."""
    entry = MockConfigEntry(
        domain="rainbird_extended",
        unique_id=rainbird_entry.entry_id,
        data={"rainbird_entry_id": rainbird_entry.entry_id},
        options={
            "program_a": {
                "frequency": "custom",
                "days": ["sun", "mon", "tue", "wed", "thu", "fri", "sat"],
                "start_time_1": "07:00:00",
                "zones": [1],
            }
        },
    )
    entry.add_to_hass(hass)
    controller.get_rain_delay.return_value = 2
    freezer.move_to(_local(5, 0))
    await _setup(hass, rainbird_entry, entry)
    # Today and tomorrow are delayed.
    assert _next(hass, 1) == dt_util.as_utc(_local(7, 0) + timedelta(days=2))
    await _unload(hass, rainbird_entry, entry)


async def test_program_off_without_zones(
    hass: HomeAssistant,
    mock_rainbird: MagicMock,
    rainbird_entry: MockConfigEntry,
) -> None:
    """A program needs zones and a start time; otherwise there's no calendar."""
    entry = MockConfigEntry(
        domain="rainbird_extended",
        unique_id=rainbird_entry.entry_id,
        data={"rainbird_entry_id": rainbird_entry.entry_id},
        options={
            "program_a": {"frequency": "custom", "start_time_1": "07:00:00"},
            "program_b": {"frequency": "cyclic", "zones": [1]},
        },
    )
    entry.add_to_hass(hass)
    await _setup(hass, rainbird_entry, entry)
    assert hass.states.get(SCHEDULE) is None
    await _unload(hass, rainbird_entry, entry)


@pytest.mark.parametrize(
    "extended_options",
    [{"disable_rainbird_switches": False, "program_a": PROGRAM_A}],
)
async def test_run_program_from_options(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    controller: MagicMock,
    mock_rainbird: MagicMock,
    rainbird_entry: MockConfigEntry,
    extended_entry: MockConfigEntry,
    active_zones: set[int],
) -> None:
    """Run program A from Home Assistant: its zones in turn, as set up."""
    freezer.move_to(_local(14, 0))
    controller.get_combined_controller_state.side_effect = RainbirdDeviceNackError()
    controller.water_budget.side_effect = RainbirdDeviceNackError()
    await _setup(hass, rainbird_entry, extended_entry)
    await _press(hass, "button.rain_bird_controller_run_program_a")
    controller.set_program.assert_awaited_once_with(0)
    active_zones.add(1)
    await _poll_core(hass, freezer)
    assert _remaining(hass, 1) == dt_util.as_utc(_local(14, 6))
    freezer.move_to(_local(14, 6, 40))
    active_zones.clear()
    active_zones.add(2)
    await _poll_core(hass, freezer)
    assert _remaining(hass, 2) == dt_util.as_utc(_local(14, 12, 30))
    await _unload(hass, rainbird_entry, extended_entry)
