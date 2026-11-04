"""Rain Bird Extended talks to Rain Bird only through services and entity state."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rainbird_extended.core_api import (
    RainbirdCore,
    calendar_zone_runs,
    program_index,
    zone_durations,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .conftest import RAINBIRD_UNIQUE_ID

UID = RAINBIRD_UNIQUE_ID


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain="rainbird",
        title="Rain Bird",
        unique_id=UID,
        data={"host": "example.com", "password": "password"},
        options={"duration": 6},
    )
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    return entry


def _switch(hass: HomeAssistant, entry: MockConfigEntry, zone: int) -> str:
    entity = er.async_get(hass).async_get_or_create(
        "switch",
        "rainbird",
        f"{UID}-{zone}",
        config_entry=entry,
        suggested_object_id=f"rain_bird_sprinkler_{zone}",
    )
    hass.states.async_set(entity.entity_id, "off", {"zone": zone})
    return entity.entity_id


async def test_start_stop_and_rain_delay_use_services(hass: HomeAssistant) -> None:
    """Commands are service calls. The config entry's runtime data is never read."""
    entry = _entry(hass)
    entry.runtime_data = object()
    switch = _switch(hass, entry, 2)
    delay = er.async_get(hass).async_get_or_create(
        "number",
        "rainbird",
        f"{UID}-rain-delay",
        config_entry=entry,
        suggested_object_id="rain_bird_controller_rain_delay",
    )
    hass.states.async_set(delay.entity_id, "0")
    calls: list[tuple[str, str, dict]] = []

    async def record(call: ServiceCall) -> None:
        calls.append((call.domain, call.service, dict(call.data)))
        if call.service == "set_value":
            hass.states.async_set(delay.entity_id, str(call.data["value"]))

    hass.services.async_register("rainbird", "start_irrigation", record)
    hass.services.async_register("switch", "turn_off", record)
    hass.services.async_register("number", "set_value", record)

    core = RainbirdCore(hass, entry)
    assert not hasattr(core, "controller")
    await core.async_start_zone(2, 10)
    await core.async_stop()
    await core.async_set_rain_delay(3)

    assert calls[0] == (
        "rainbird",
        "start_irrigation",
        {"entity_id": switch, "duration": 10},
    )
    assert calls[1][0:2] == ("switch", "turn_off")
    assert calls[1][2]["entity_id"] == switch
    assert calls[2] == (
        "number",
        "set_value",
        {"entity_id": delay.entity_id, "value": 3},
    )
    assert core.rain_delay_days() == 3
    # Reading the delay did not touch the sentinel runtime data.
    assert type(entry.runtime_data) is object


async def test_run_program_presses_the_core_button(hass: HomeAssistant) -> None:
    """Run program presses Rain Bird's button instead of calling the controller."""
    entry = _entry(hass)
    button = er.async_get(hass).async_get_or_create(
        "button",
        "rainbird",
        f"{UID}-program-1",
        config_entry=entry,
        suggested_object_id="rain_bird_controller_run_program_b",
    )
    pressed: list[str] = []

    async def record(call: ServiceCall) -> None:
        pressed.append(call.data["entity_id"])

    hass.services.async_register("button", "press", record)
    core = RainbirdCore(hass, entry)
    assert core.program_buttons() == {1: button.entity_id}
    await core.async_run_program(1)
    assert pressed == [button.entity_id]


def test_calendar_description_lists_zones_in_order() -> None:
    """Zone lines on a calendar event become that zone's start and end."""
    assert zone_durations("Zone 1: 10 min\nZone 2: 15 min") == [(1, 10), (2, 15)]
    assert zone_durations("Program A") == []
    runs, patterns = calendar_zone_runs(
        [
            {
                "summary": "PGM A",
                "start": "2026-11-04T05:00:00+00:00",
                "end": "2026-11-04T05:25:00+00:00",
                "description": "Zone 1: 10 min\nZone 2: 15 min",
            }
        ]
    )
    assert runs[0][0] == 1
    assert runs[1][0] == 2
    assert runs[1][1] - runs[0][1] == timedelta(minutes=10)
    assert runs[1][2] - runs[1][1] == timedelta(minutes=15)
    assert patterns[0][1] == (2, timedelta(minutes=10), timedelta(minutes=15))
    assert program_index(None) is None
    assert program_index("Morning") is None
    assert program_index("Program B") == 1
    # Zero-length lines, a date-only start and a summary without a program
    # don't become runs. A naive datetime is treated as local.
    runs, patterns = calendar_zone_runs(
        [
            {"summary": "PGM A", "start": "2026-11-04", "description": "Zone 1: 0 min"},
            {
                "summary": "notes",
                "start": datetime(2026, 11, 4, 6, 0),
                "description": "Zone 3: 5 min",
            },
            {"description": "Zone 1: 10 min"},
        ]
    )
    assert [run[0] for run in runs] == [3]
    assert runs[0][1].tzinfo is not None
    assert patterns == {}
    assert zone_durations(None) == []


async def test_disabled_and_unavailable_switches(hass: HomeAssistant) -> None:
    """A disabled switch still names the zone; unavailable means it can't run."""
    entry = _entry(hass)
    enabled = _switch(hass, entry, 1)
    disabled = er.async_get(hass).async_get_or_create(
        "switch",
        "rainbird",
        f"{UID}-2",
        config_entry=entry,
        suggested_object_id="rain_bird_sprinkler_2",
    )
    hass.states.async_set(enabled, STATE_UNAVAILABLE, {"zone": 1})
    core = RainbirdCore(hass, entry)
    assert core.reachable() is False
    assert core.zone_reachable(2) is False
    assert core.zone_reachable(9) is False

    hass.states.async_remove(enabled)
    # Only a disabled switch (no state) left: the entry is loaded, so reachable.
    assert disabled.entity_id not in hass.states.async_entity_ids()
    assert core.reachable() is True
    assert core.zone_reachable(2) is True

    with pytest.raises(HomeAssistantError, match="disabled"):
        await core.async_start_zone(2, 5)
    with pytest.raises(HomeAssistantError, match="no switch"):
        await core.async_start_zone(9, 5)
    with pytest.raises(HomeAssistantError, match="disabled"):
        await core.async_stop()

    hass.states.async_set(enabled, "on", {"zone": 1})
    stopped: list[str] = []

    async def turn_off(call: ServiceCall) -> None:
        stopped.append(call.data["entity_id"])

    hass.services.async_register("switch", "turn_off", turn_off)
    await core.async_stop()
    assert stopped == [enabled]


async def test_rain_delay_sensor_and_service(hass: HomeAssistant) -> None:
    """Rain delay is read from the sensor and set by the service when it exists."""
    entry = MockConfigEntry(
        domain="rainbird",
        title="Rain Bird",
        unique_id=UID,
        options={"duration": "nope"},
    )
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    sensor = er.async_get(hass).async_get_or_create(
        "sensor",
        "rainbird",
        f"{UID}-raindelay",
        config_entry=entry,
        suggested_object_id="rain_bird_controller_raindelay",
    )
    rain = er.async_get(hass).async_get_or_create(
        "binary_sensor",
        "rainbird",
        f"{UID}-rainsensor",
        config_entry=entry,
        suggested_object_id="rain_bird_controller_rainsensor",
    )
    core = RainbirdCore(hass, entry)
    assert core.default_duration_minutes == 6
    assert core.rain_delay_days() == 0
    assert core.rain_sensor_on() is None
    hass.states.async_set(sensor.entity_id, "4")
    hass.states.async_set(rain.entity_id, "on")
    assert core.rain_delay_days() == 4
    assert core.rain_sensor_on() is True
    hass.states.async_set(sensor.entity_id, "later")
    hass.states.async_set(rain.entity_id, STATE_UNKNOWN)
    assert core.rain_delay_days() == 0
    assert core.rain_sensor_on() is None

    calls: list[int] = []

    async def set_delay(call: ServiceCall) -> None:
        calls.append(call.data["duration"])

    hass.services.async_register("rainbird", "set_rain_delay", set_delay)
    await core.async_set_rain_delay(2)
    assert calls == [2]

    entry.mock_state(hass, ConfigEntryState.NOT_LOADED)
    with pytest.raises(HomeAssistantError, match="not loaded"):
        await core.async_set_rain_delay(1)


async def test_missing_program_button_and_calendar(hass: HomeAssistant) -> None:
    """Without a Run program button or a calendar, those calls fail closed."""
    entry = _entry(hass)
    er.async_get(hass).async_get_or_create(
        "button",
        "rainbird",
        f"{UID}-program-sync",
        config_entry=entry,
    )
    er.async_get(hass).async_get_or_create(
        "button",
        "rainbird",
        f"{UID}-sync-time",
        config_entry=entry,
    )
    core = RainbirdCore(hass, entry)
    assert core.program_buttons() == {}
    assert core.calendar_entity_id() is None
    with pytest.raises(HomeAssistantError, match=r"2026\.10"):
        await core.async_run_program(0)
    assert await core.async_calendar_events(dt_util.utcnow(), dt_util.utcnow()) is None

    calendar = er.async_get(hass).async_get_or_create(
        "calendar",
        "rainbird",
        UID,
        config_entry=entry,
        suggested_object_id="rain_bird_controller",
    )

    async def fail(_call: ServiceCall) -> None:
        raise HomeAssistantError("no schedule yet")

    hass.services.async_register(
        "calendar",
        "get_events",
        fail,
        supports_response=SupportsResponse.ONLY,
    )
    assert await core.async_calendar_events(dt_util.utcnow(), dt_util.utcnow()) is None

    async def respond(call: ServiceCall) -> dict:
        return {calendar.entity_id: {"events": [{"summary": "PGM A"}, "skip-me"]}}

    hass.services.async_remove("calendar", "get_events")
    hass.services.async_register(
        "calendar",
        "get_events",
        respond,
        supports_response=SupportsResponse.ONLY,
    )
    events = await core.async_calendar_events(dt_util.utcnow(), dt_util.utcnow())
    assert events == [{"summary": "PGM A"}]

    with pytest.raises(HomeAssistantError, match="Rain delay"):
        await core.async_set_rain_delay(1)
    with pytest.raises(HomeAssistantError, match="no zone switches"):
        await core.async_stop()
