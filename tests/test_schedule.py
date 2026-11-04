"""Tests for the Irrigating sensor, schedule-based time remaining and seasonal adjustment cleanup."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.components.valve import (
    DOMAIN as VALVE_DOMAIN,
    SERVICE_CLOSE_VALVE,
    SERVICE_OPEN_VALVE,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .conftest import RAINBIRD_UNIQUE_ID
from .helpers import _local, _poll_core, _setup, _unload, zone_calendar_events

_CALENDAR = (
    "custom_components.rainbird_extended.coordinator.RainbirdCore.async_calendar_events"
)
_ZONE_LINES = "Zone 1: 10 min\nZone 2: 15 min"

IRRIGATING = "binary_sensor.rain_bird_controller_irrigating"


def _remaining(hass: HomeAssistant, zone: int) -> datetime | None:
    state = hass.states.get(f"sensor.rain_bird_sprinkler_{zone}_time_remaining")
    return dt_util.parse_datetime(state.state)


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


async def test_time_remaining_from_schedule(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_rainbird: MagicMock,
    rainbird_entry: MockConfigEntry,
    extended_entry: MockConfigEntry,
    active_zones: set[int],
) -> None:
    """A running zone ends when the calendar's zone line says it does.

    Program A is zone 1 for 10 min then zone 2 for 15 min from 05:00. The
    controller's seasonal adjust isn't on any Rain Bird entity, so the times
    are used as the calendar lists them.
    """
    freezer.move_to(_local(5, 12))
    active_zones.add(2)
    events = zone_calendar_events(_ZONE_LINES)

    async def fake(_core: object, _start: object, _end: object) -> list[dict[str, str]]:
        return events

    with patch(_CALENDAR, fake):
        await _setup(hass, rainbird_entry, extended_entry)
        await _poll_core(hass, freezer)
    assert _remaining(hass, 2) == dt_util.as_utc(_local(5, 25))
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
    events = zone_calendar_events(_ZONE_LINES)

    async def fake(_core: object, _start: object, _end: object) -> list[dict[str, str]]:
        return events

    with patch(_CALENDAR, fake):
        await _setup(hass, rainbird_entry, extended_entry)
        if hass.states.get("button.rain_bird_controller_run_program_a") is None:
            pytest.skip("Rain Bird Run program buttons need Home Assistant 2026.10")

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


async def test_stale_seasonal_adjustment_removed(
    hass: HomeAssistant,
    mock_rainbird: MagicMock,
    rainbird_entry: MockConfigEntry,
    extended_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Seasonal adjustment and weather adjustment entities are removed."""
    seasonal = entity_registry.async_get_or_create(
        "sensor",
        "rainbird_extended",
        f"{RAINBIRD_UNIQUE_ID}-seasonal_adjustment",
        config_entry=extended_entry,
        suggested_object_id="rain_bird_controller_seasonal_adjustment",
    )
    weather = entity_registry.async_get_or_create(
        "switch",
        "rainbird_extended",
        f"{RAINBIRD_UNIQUE_ID}-weather_adjustment",
        config_entry=extended_entry,
        suggested_object_id="rain_bird_controller_weather_adjustment",
    )
    assert await hass.config_entries.async_setup(rainbird_entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(extended_entry.entry_id)
    await hass.async_block_till_done()

    assert entity_registry.async_get(seasonal.entity_id) is None
    assert entity_registry.async_get(weather.entity_id) is None
    assert hass.states.get("number.rain_bird_controller_seasonal_adjustment_a") is None
    await hass.config_entries.async_unload(extended_entry.entry_id)
    await hass.config_entries.async_unload(rainbird_entry.entry_id)


async def test_schedule_is_read_from_the_calendar(
    hass: HomeAssistant,
    mock_rainbird: MagicMock,
    rainbird_entry: MockConfigEntry,
    extended_entry: MockConfigEntry,
) -> None:
    """Upcoming runs come from calendar.get_events, not Rain Bird's loader."""
    await _setup(hass, rainbird_entry)
    schedule = rainbird_entry.runtime_data.schedule_coordinator
    schedule.async_load = MagicMock()
    calls: list[object] = []

    async def fake(_core: object, start: object, end: object) -> list[dict[str, str]]:
        calls.append((start, end))
        return []

    with patch(_CALENDAR, fake):
        await _setup(hass, extended_entry)
    assert calls
    schedule.async_load.assert_not_called()
    await _unload(hass, rainbird_entry, extended_entry)
