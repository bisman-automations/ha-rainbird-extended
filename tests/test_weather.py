"""Tests for rain skip, freeze skip and weather adjustment."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.rainbird_extended.const import (
    CONF_ADJUST_HIGH_PERCENT,
    CONF_ADJUST_HIGH_TEMPERATURE,
    CONF_ADJUST_LOW_PERCENT,
    CONF_ADJUST_LOW_TEMPERATURE,
    CONF_DISABLE_RAINBIRD_SWITCHES,
    CONF_FREEZE_DELAY_DAYS,
    CONF_FREEZE_SENSOR,
    CONF_FREEZE_TEMPERATURE,
    CONF_MOISTURE_SENSOR,
    CONF_MOISTURE_THRESHOLD,
    CONF_RAIN_CHANCE,
    CONF_RAIN_CHECK_TIME,
    CONF_RAIN_DELAY_DAYS,
    CONF_TEMPERATURE_UNIT,
    CONF_WEATHER_ENTITY,
    EVENT_FREEZE_SKIP,
    EVENT_MOISTURE_SKIP,
    EVENT_RAIN_SKIP,
    EVENT_WEATHER_ADJUSTMENT,
)
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.components.switch import (
    DOMAIN as SWITCH_DOMAIN,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
)
from homeassistant.components.valve import DOMAIN as VALVE_DOMAIN, SERVICE_OPEN_VALVE
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.util import dt as dt_util

from .helpers import _local, _setup, _unload

WEATHER = "weather.home"
SENSOR = "sensor.outside_temperature"
FREEZE = "switch.rain_bird_controller_freeze_skip"
ADJUST = "switch.rain_bird_controller_weather_adjustment"
RAIN_SKIP = "switch.rain_bird_controller_rain_skip"
VALVE = "valve.rain_bird_sprinkler_2"
BASE = {
    CONF_DISABLE_RAINBIRD_SWITCHES: False,
    CONF_RAIN_CHECK_TIME: "04:00:00",
    CONF_TEMPERATURE_UNIT: "°C",
}


WEATHER_OPTIONS = {**BASE, CONF_WEATHER_ENTITY: WEATHER}


def _weather(hass: HomeAssistant, forecast: dict[str, Any], unit: str = "°C") -> None:
    hass.states.async_set(WEATHER, "cloudy", {"temperature_unit": unit})

    async def get_forecasts(call: ServiceCall) -> dict[str, Any]:
        return {
            WEATHER: {"forecast": [{"datetime": dt_util.now().isoformat(), **forecast}]}
        }

    hass.services.async_register(
        "weather",
        "get_forecasts",
        get_forecasts,
        supports_response=SupportsResponse.ONLY,
    )


def _sensor(hass: HomeAssistant, value: float, unit: str = "°C") -> None:
    hass.states.async_set(
        SENSOR, str(value), {"unit_of_measurement": unit, "device_class": "temperature"}
    )


async def _check_time(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(_local(4, 0, 0))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def _rain_weather(hass: HomeAssistant, forecast: list[dict[str, Any]]) -> None:
    """A weather entity whose daily forecast is the given entries."""
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


@pytest.mark.parametrize(
    "extended_options",
    [{**WEATHER_OPTIONS, CONF_FREEZE_TEMPERATURE: 2, CONF_FREEZE_DELAY_DAYS: 2}],
)
@pytest.mark.parametrize(
    ("low", "unit", "skipped"),
    [(0, "°C", True), (2, "°C", True), (5, "°C", False), (30, "°F", True)],
)
async def test_freeze_skip_forecast(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    controller: MagicMock,
    mock_rainbird: MagicMock,
    rainbird_entry: MockConfigEntry,
    extended_entry: MockConfigEntry,
    low: float,
    unit: str,
    skipped: bool,
) -> None:
    """A forecast low at or below the freeze temperature sets the rain delay."""
    freezer.move_to(_local(3, 59, 50))
    # Rain chance 0 so rain skip stays out of it.
    _weather(hass, {"templow": low, "precipitation_probability": 0}, unit)
    events = async_capture_events(hass, EVENT_FREEZE_SKIP)
    await _setup(hass, rainbird_entry, extended_entry)
    assert hass.states.get(FREEZE).state == "on"

    await _check_time(hass, freezer)

    state = hass.states.get(FREEZE)
    assert state.attributes["last_check"] is not None
    if skipped:
        controller.set_rain_delay.assert_awaited_once_with(2)
        assert events[0].data["reason"] == "forecast"
        assert state.attributes["last_skipped"] is not None
    else:
        controller.set_rain_delay.assert_not_awaited()
        assert not events
    await _unload(hass, rainbird_entry, extended_entry)


@pytest.mark.parametrize(
    "extended_options",
    [{**BASE, CONF_FREEZE_SENSOR: SENSOR, CONF_FREEZE_TEMPERATURE: 2}],
)
async def test_freeze_skip_sensor(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    controller: MagicMock,
    mock_rainbird: MagicMock,
    rainbird_entry: MockConfigEntry,
    extended_entry: MockConfigEntry,
) -> None:
    """With only a sensor: no rain skip, and watering stops when it freezes."""
    freezer.move_to(_local(3, 0, 0))
    _sensor(hass, 6)
    events = async_capture_events(hass, EVENT_FREEZE_SKIP)
    await _setup(hass, rainbird_entry, extended_entry)
    assert hass.states.get(FREEZE).state == "on"
    assert hass.states.get(RAIN_SKIP) is None
    assert hass.states.get(ADJUST) is None

    await hass.services.async_call(
        VALVE_DOMAIN, SERVICE_OPEN_VALVE, {ATTR_ENTITY_ID: VALVE}, blocking=True
    )
    await hass.async_block_till_done()
    _sensor(hass, 3)
    await hass.async_block_till_done()
    controller.stop_irrigation.assert_not_awaited()

    _sensor(hass, 1.5)
    await hass.async_block_till_done()
    controller.stop_irrigation.assert_awaited_once()
    assert events[0].data["reason"] == "sensor"
    assert hass.states.get(VALVE).state == "closed"
    assert hass.states.get(FREEZE).attributes["last_stopped"] is not None

    # Starting a zone while it's freezing stops it again.
    await hass.services.async_call(
        VALVE_DOMAIN, SERVICE_OPEN_VALVE, {ATTR_ENTITY_ID: VALVE}, blocking=True
    )
    await hass.async_block_till_done()
    assert controller.stop_irrigation.await_count == 2

    # At the check time the sensor counts as today's low.
    await _check_time(hass, freezer)
    controller.set_rain_delay.assert_awaited_once_with(1)
    await _unload(hass, rainbird_entry, extended_entry)


@pytest.mark.parametrize(
    "extended_options",
    [{**BASE, CONF_FREEZE_SENSOR: SENSOR, CONF_FREEZE_TEMPERATURE: 2}],
)
async def test_freeze_skip_leaves_blowout_alone(
    hass: HomeAssistant,
    controller: MagicMock,
    mock_rainbird: MagicMock,
    rainbird_entry: MockConfigEntry,
    extended_entry: MockConfigEntry,
) -> None:
    """Blowouts are air and done in the cold, so they aren't stopped."""
    _sensor(hass, -5)
    await _setup(hass, rainbird_entry, extended_entry)
    await hass.services.async_call(
        BUTTON_DOMAIN,
        SERVICE_PRESS,
        {ATTR_ENTITY_ID: "button.rain_bird_controller_blowout_sprinklers"},
        blocking=True,
    )
    _sensor(hass, -6)
    await hass.async_block_till_done()
    controller.stop_irrigation.assert_not_awaited()
    await _unload(hass, rainbird_entry, extended_entry)


async def test_no_weather_switches_by_default(
    hass: HomeAssistant, setup_integrations: MagicMock
) -> None:
    """Nothing is added until a weather entity or sensor is chosen."""
    assert hass.states.get(FREEZE) is None
    assert hass.states.get(ADJUST) is None


@pytest.mark.parametrize(
    "extended_options",
    [
        {
            **WEATHER_OPTIONS,
            CONF_ADJUST_LOW_TEMPERATURE: 15,
            CONF_ADJUST_LOW_PERCENT: 60,
            CONF_ADJUST_HIGH_TEMPERATURE: 35,
            CONF_ADJUST_HIGH_PERCENT: 150,
        }
    ],
)
@pytest.mark.parametrize(
    ("high", "unit", "percent"),
    [
        (25, "°C", 105),
        (27, "°C", 115),  # 114 rounded to 5
        (40, "°C", 150),
        (5, "°C", 60),
        (77, "°F", 105),  # 25°C
    ],
)
async def test_weather_adjustment(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    controller: MagicMock,
    mock_rainbird: MagicMock,
    rainbird_entry: MockConfigEntry,
    extended_entry: MockConfigEntry,
    high: float,
    unit: str,
    percent: int,
) -> None:
    """Off until turned on; then each program gets the forecast's percent."""
    freezer.move_to(_local(3, 59, 50))
    _weather(hass, {"temperature": high, "precipitation_probability": 0}, unit)
    events = async_capture_events(hass, EVENT_WEATHER_ADJUSTMENT)
    await _setup(hass, rainbird_entry, extended_entry)
    assert hass.states.get(ADJUST).state == "off"
    await _check_time(hass, freezer)
    controller.set_water_budget.assert_not_awaited()

    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: ADJUST}, blocking=True
    )
    freezer.move_to(_local(3, 59, 50) + dt_util.dt.timedelta(days=1))
    await hass.async_block_till_done()
    freezer.move_to(_local(4, 0, 0))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    calls = [call.args for call in controller.set_water_budget.await_args_list]
    if percent == 100:
        assert calls == []
    else:
        assert calls == [(0, percent), (1, percent), (2, percent)]
    state = hass.states.get(ADJUST)
    assert state.attributes["last_percent"] == percent
    assert events[-1].data["percent"] == percent
    assert hass.states.get(
        "number.rain_bird_controller_seasonal_adjustment_a"
    ).state == str(percent)
    await _unload(hass, rainbird_entry, extended_entry)


@pytest.mark.parametrize("extended_options", [WEATHER_OPTIONS])
async def test_no_weather_adjustment_when_read_only(
    hass: HomeAssistant,
    controller: MagicMock,
    mock_rainbird: MagicMock,
    rainbird_entry: MockConfigEntry,
    extended_entry: MockConfigEntry,
) -> None:
    """Without set_water_budget (Home Assistant 2026.9) there's no switch."""
    del controller.set_water_budget
    _weather(hass, {"temperature": 20})
    await _setup(hass, rainbird_entry, extended_entry)
    assert hass.states.get(ADJUST) is None
    assert hass.states.get(FREEZE) is not None
    await _unload(hass, rainbird_entry, extended_entry)


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
    _rain_weather(hass, [{"datetime": dt_util.now().isoformat(), **forecast}])
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
    _rain_weather(
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


MOISTURE = "sensor.lawn_moisture"
MOISTURE_SKIP = "switch.rain_bird_controller_soil_moisture_skip"


@pytest.mark.parametrize(
    "extended_options",
    [{**BASE, CONF_MOISTURE_SENSOR: MOISTURE, CONF_MOISTURE_THRESHOLD: 45}],
)
@pytest.mark.parametrize(("moisture", "skipped"), [(50, True), (45, True), (30, False)])
async def test_moisture_skip(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    controller: MagicMock,
    mock_rainbird: MagicMock,
    rainbird_entry: MockConfigEntry,
    extended_entry: MockConfigEntry,
    moisture: float,
    skipped: bool,
) -> None:
    """At the check time, wet enough soil sets the rain delay."""
    freezer.move_to(_local(3, 59, 50))
    hass.states.async_set(MOISTURE, str(moisture), {"unit_of_measurement": "%"})
    events = async_capture_events(hass, EVENT_MOISTURE_SKIP)
    await _setup(hass, rainbird_entry, extended_entry)
    assert hass.states.get(MOISTURE_SKIP).state == "on"
    assert hass.states.get(RAIN_SKIP) is None

    await _check_time(hass, freezer)

    state = hass.states.get(MOISTURE_SKIP)
    assert state.attributes["last_moisture"] == moisture
    if skipped:
        controller.set_rain_delay.assert_awaited_once_with(1)
        assert events[0].data["moisture"] == moisture
    else:
        controller.set_rain_delay.assert_not_awaited()
        assert not events
    await _unload(hass, rainbird_entry, extended_entry)


@pytest.mark.parametrize("extended_options", [{**BASE, CONF_MOISTURE_SENSOR: MOISTURE}])
async def test_moisture_skip_unavailable_sensor(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    controller: MagicMock,
    mock_rainbird: MagicMock,
    rainbird_entry: MockConfigEntry,
    extended_entry: MockConfigEntry,
) -> None:
    """No reading: nothing is skipped."""
    freezer.move_to(_local(3, 59, 50))
    hass.states.async_set(MOISTURE, "unavailable")
    await _setup(hass, rainbird_entry, extended_entry)
    await _check_time(hass, freezer)
    controller.set_rain_delay.assert_not_awaited()
    assert hass.states.get(MOISTURE_SKIP).attributes["last_check"] is not None
    await _unload(hass, rainbird_entry, extended_entry)
