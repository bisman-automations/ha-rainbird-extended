"""Tests for the irrigating sensor, schedule-based time remaining and rain skip."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
from pyrainbird.data import WaterBudget
from pyrainbird.exceptions import RainbirdDeviceNackError
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.rainbird_extended.const import (
    CONF_DISABLE_RAINBIRD_SWITCHES,
    CONF_RAIN_CHANCE,
    CONF_RAIN_CHECK_TIME,
    CONF_RAIN_DELAY_DAYS,
    CONF_WEATHER_ENTITY,
    EVENT_RAIN_SKIP,
)
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN, SERVICE_TURN_OFF
from homeassistant.components.valve import (
    DOMAIN as VALVE_DOMAIN,
    SERVICE_CLOSE_VALVE,
    SERVICE_OPEN_VALVE,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.util import dt as dt_util

from .test_entities import _poll_core

IRRIGATING = "binary_sensor.rain_bird_controller_irrigating"
RAIN_SKIP = "switch.rain_bird_controller_rain_skip"
WEATHER = "weather.home"


def _local(hour: int, minute: int, second: int = 0) -> datetime:
    """Today at a local time."""
    return dt_util.now().replace(hour=hour, minute=minute, second=second, microsecond=0)


def _remaining(hass: HomeAssistant, zone: int) -> datetime | None:
    state = hass.states.get(f"sensor.rain_bird_sprinkler_{zone}_time_remaining")
    return dt_util.parse_datetime(state.state)


async def _setup(hass: HomeAssistant, *entries: MockConfigEntry) -> None:
    for entry in entries:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def _unload(hass: HomeAssistant, *entries: MockConfigEntry) -> None:
    for entry in reversed(entries):
        await hass.config_entries.async_unload(entry.entry_id)


async def test_irrigating(hass: HomeAssistant, setup_integrations: MagicMock) -> None:
    """On while a zone runs, with the zone and end time."""
    assert hass.states.get(IRRIGATING).state == "off"
    await hass.services.async_call(
        VALVE_DOMAIN,
        SERVICE_OPEN_VALVE,
        {ATTR_ENTITY_ID: "valve.rain_bird_sprinkler_2"},
        blocking=True,
    )
    await hass.async_block_till_done()
    state = hass.states.get(IRRIGATING)
    assert state.state == "on"
    assert state.attributes["device_class"] == "running"
    assert state.attributes["zones"] == [2]
    end = dt_util.parse_datetime(state.attributes["end"])
    assert abs(end - _remaining(hass, 2)) < timedelta(seconds=1)
    await hass.services.async_call(
        VALVE_DOMAIN,
        SERVICE_CLOSE_VALVE,
        {ATTR_ENTITY_ID: "valve.rain_bird_sprinkler_2"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert hass.states.get(IRRIGATING).state == "off"


@pytest.mark.parametrize(
    ("budget", "now", "end"), [(100, (5, 12), (5, 25)), (50, (5, 7), (5, 12, 30))]
)
async def test_time_remaining_from_schedule(
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
    """A scheduled run of a controller that doesn't report remaining time.

    Program A runs zone 1 for 10 min then zone 2 for 15 min from 05:00, both
    scaled by the program's seasonal adjust.
    """
    freezer.move_to(_local(*now))
    active_zones.add(2)
    controller.get_combined_controller_state.side_effect = RainbirdDeviceNackError()
    controller.water_budget.side_effect = lambda key: WaterBudget(key, budget)
    await _setup(hass, rainbird_entry, extended_entry)
    await hass.config_entries.async_entries("rainbird")[
        0
    ].runtime_data.schedule_coordinator.async_refresh()
    await _poll_core(hass, freezer)
    assert _remaining(hass, 2) == dt_util.as_utc(_local(*end))
    await _unload(hass, rainbird_entry, extended_entry)


async def test_time_remaining_for_manual_program(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    controller: MagicMock,
    mock_rainbird: MagicMock,
    rainbird_entry: MockConfigEntry,
    extended_entry: MockConfigEntry,
    active_zones: set[int],
) -> None:
    """A program started from Home Assistant: each zone in turn."""
    freezer.move_to(_local(14, 0))
    controller.get_combined_controller_state.side_effect = RainbirdDeviceNackError()
    await _setup(hass, rainbird_entry, extended_entry)
    await hass.config_entries.async_entries("rainbird")[
        0
    ].runtime_data.schedule_coordinator.async_refresh()

    await hass.services.async_call(
        BUTTON_DOMAIN,
        SERVICE_PRESS,
        {ATTR_ENTITY_ID: "button.rain_bird_controller_run_program_a"},
        blocking=True,
    )
    active_zones.add(1)
    await _poll_core(hass, freezer)
    assert _remaining(hass, 1) == dt_util.as_utc(_local(14, 10))

    freezer.move_to(_local(14, 10, 30))
    active_zones.clear()
    active_zones.add(2)
    await _poll_core(hass, freezer)
    assert _remaining(hass, 2) == dt_util.as_utc(_local(14, 25))
    await _unload(hass, rainbird_entry, extended_entry)


def _weather(hass: HomeAssistant, forecast: list[dict[str, Any]]) -> None:
    hass.states.async_set(WEATHER, "cloudy")

    async def get_forecasts(call: ServiceCall) -> dict[str, Any]:
        return {WEATHER: {"forecast": forecast}}

    hass.services.async_register(
        "weather",
        "get_forecasts",
        get_forecasts,
        supports_response=SupportsResponse.ONLY,
    )


RAIN_OPTIONS = {
    CONF_DISABLE_RAINBIRD_SWITCHES: False,
    CONF_WEATHER_ENTITY: WEATHER,
    CONF_RAIN_CHANCE: 60,
    CONF_RAIN_CHECK_TIME: "04:00:00",
    CONF_RAIN_DELAY_DAYS: 2,
}


@pytest.mark.parametrize("extended_options", [RAIN_OPTIONS])
@pytest.mark.parametrize(
    ("forecast", "skipped"),
    [
        ({"precipitation_probability": 80, "condition": "rainy"}, True),
        ({"precipitation_probability": 30, "condition": "rainy"}, False),
        ({"condition": "pouring"}, True),
        ({"condition": "sunny"}, False),
    ],
)
async def test_rain_skip(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    controller: MagicMock,
    mock_rainbird: MagicMock,
    rainbird_entry: MockConfigEntry,
    extended_entry: MockConfigEntry,
    forecast: dict[str, Any],
    skipped: bool,
) -> None:
    """At the check time, a wet enough forecast sets the rain delay."""
    freezer.move_to(_local(3, 59, 50))
    _weather(hass, [{"datetime": dt_util.now().isoformat(), **forecast}])
    events = async_capture_events(hass, EVENT_RAIN_SKIP)
    await _setup(hass, rainbird_entry, extended_entry)
    assert hass.states.get(RAIN_SKIP).state == "on"

    freezer.move_to(_local(4, 0, 0))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    state = hass.states.get(RAIN_SKIP)
    assert state.attributes["last_check"] is not None
    if skipped:
        controller.set_rain_delay.assert_awaited_once_with(2)
        assert len(events) == 1
        assert events[0].data["rain_delay_days"] == 2
        assert state.attributes["last_skipped"] is not None
    else:
        controller.set_rain_delay.assert_not_awaited()
        assert not events
    await _unload(hass, rainbird_entry, extended_entry)


@pytest.mark.parametrize("extended_options", [RAIN_OPTIONS])
async def test_rain_skip_off_or_already_delayed(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    controller: MagicMock,
    mock_rainbird: MagicMock,
    rainbird_entry: MockConfigEntry,
    extended_entry: MockConfigEntry,
) -> None:
    """Nothing happens when switched off, or when a longer delay is set."""
    freezer.move_to(_local(3, 59, 50))
    _weather(
        hass,
        [{"datetime": dt_util.now().isoformat(), "precipitation_probability": 90}],
    )
    await _setup(hass, rainbird_entry, extended_entry)
    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: RAIN_SKIP}, blocking=True
    )
    freezer.move_to(_local(4, 0, 0))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    controller.set_rain_delay.assert_not_awaited()

    # Back on, but the controller already has a 3 day delay.
    await hass.services.async_call(
        SWITCH_DOMAIN, "turn_on", {ATTR_ENTITY_ID: RAIN_SKIP}, blocking=True
    )
    controller.get_rain_delay.return_value = 3
    await hass.config_entries.async_entries("rainbird")[
        0
    ].runtime_data.coordinator.async_refresh()
    freezer.move_to(_local(4, 0, 0) + timedelta(days=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    controller.set_rain_delay.assert_not_awaited()
    await _unload(hass, rainbird_entry, extended_entry)


async def test_no_rain_skip_without_weather(
    hass: HomeAssistant, setup_integrations: MagicMock
) -> None:
    """The switch only exists once a weather entity is chosen."""
    assert hass.states.get(RAIN_SKIP) is None
