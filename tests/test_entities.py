"""Tests for the valve, valve runtime and time remaining entities."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from freezegun.api import FrozenDateTimeFactory
from pyrainbird.exceptions import RainbirdDeviceBusyException, RainbirdDeviceNackError
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache_with_extra_data,
)

from homeassistant.components.number import (
    ATTR_VALUE,
    DOMAIN as NUMBER_DOMAIN,
    SERVICE_SET_VALUE,
)
from homeassistant.components.valve import (
    DOMAIN as VALVE_DOMAIN,
    SERVICE_CLOSE_VALVE,
    SERVICE_OPEN_VALVE,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util

from .conftest import RAINBIRD_UNIQUE_ID, ZONES, controller_state
from .helpers import _poll_core

VALVE = "valve.rain_bird_sprinkler_2"
SWITCH = "switch.rain_bird_sprinkler_2"
RUNTIME = "number.rain_bird_sprinkler_2_valve_runtime"
REMAINING = "sensor.rain_bird_sprinkler_2_time_remaining"


@pytest.mark.usefixtures("setup_integrations")
async def test_entities_linked_to_zone_devices(
    hass: HomeAssistant,
    rainbird_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Each zone gets a valve, runtime and time remaining on its existing device."""
    for zone in ZONES:
        device = device_registry.async_get_device_by_identifier(
            ("rainbird", f"{RAINBIRD_UNIQUE_ID}-{zone}"), rainbird_entry.entry_id
        )
        assert device is not None
        assert device.name == f"Rain Bird Sprinkler {zone}"
        domains = {
            entry.domain
            for entry in er.async_entries_for_device(entity_registry, device.id)
        }
        assert domains == {"switch", "valve", "number", "sensor", "event"}
    # No extra devices were created.
    assert not dr.async_entries_for_config_entry(
        device_registry,
        hass.config_entries.async_entries("rainbird_extended")[0].entry_id,
    )

    valve = hass.states.get(VALVE)
    assert valve.state == "closed"
    assert valve.attributes["device_class"] == "water"
    assert valve.attributes["zone"] == 2
    assert hass.states.get(RUNTIME).state == "360"  # core default of 6 minutes
    assert hass.states.get(RUNTIME).attributes["unit_of_measurement"] == "s"
    assert hass.states.get(REMAINING).state == STATE_UNKNOWN
    assert hass.states.get(REMAINING).attributes["device_class"] == "timestamp"


async def test_open_valve_uses_runtime(
    hass: HomeAssistant,
    setup_integrations: MagicMock,
    active_zones: set[int],
    freezer: FrozenDateTimeFactory,
) -> None:
    """Opening the valve runs the zone for its runtime and tracks the end time."""
    controller = setup_integrations
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: RUNTIME, ATTR_VALUE: 600},
        blocking=True,
    )
    assert hass.states.get(RUNTIME).state == "600"

    start = dt_util.utcnow()
    await hass.services.async_call(
        VALVE_DOMAIN, SERVICE_OPEN_VALVE, {ATTR_ENTITY_ID: VALVE}, blocking=True
    )
    await hass.async_block_till_done()

    controller.irrigate_zone.assert_awaited_once_with(2, 10)
    assert hass.states.get(VALVE).state == "open"
    assert hass.states.get(SWITCH).state == "on"  # core switch follows along
    end = dt_util.parse_datetime(hass.states.get(REMAINING).state)
    assert abs(end - (start + timedelta(minutes=10))) < timedelta(seconds=1)

    # Controller keeps reporting the zone running.
    await _poll_core(hass, freezer)
    assert hass.states.get(VALVE).state == "open"
    assert dt_util.parse_datetime(hass.states.get(REMAINING).state) == end

    # Run finishes.
    active_zones.clear()
    await _poll_core(hass, freezer)
    assert hass.states.get(VALVE).state == "closed"
    assert hass.states.get(REMAINING).state == STATE_UNKNOWN


async def test_close_valve_stops(
    hass: HomeAssistant, setup_integrations: MagicMock
) -> None:
    """Closing the valve stops irrigation and clears time remaining."""
    controller = setup_integrations
    await hass.services.async_call(
        VALVE_DOMAIN, SERVICE_OPEN_VALVE, {ATTR_ENTITY_ID: VALVE}, blocking=True
    )
    await hass.services.async_call(
        VALVE_DOMAIN, SERVICE_CLOSE_VALVE, {ATTR_ENTITY_ID: VALVE}, blocking=True
    )
    await hass.async_block_till_done()
    controller.stop_irrigation.assert_awaited_once()
    assert hass.states.get(VALVE).state == "closed"
    assert hass.states.get(SWITCH).state == "off"
    assert hass.states.get(REMAINING).state == STATE_UNKNOWN


@pytest.mark.parametrize("active_zones", [{2}])
async def test_controller_reported_remaining(
    hass: HomeAssistant,
    controller: MagicMock,
    setup_integrations: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A run started elsewhere uses the controller's remaining run time."""
    assert hass.states.get(VALVE).state == "open"
    controller.get_combined_controller_state.return_value = controller_state(
        remaining=300, station=2
    )
    await _poll_core(hass, freezer)
    end = dt_util.parse_datetime(hass.states.get(REMAINING).state)
    # Within a few seconds (polling advances the clock).
    assert abs(end - (dt_util.utcnow() + timedelta(seconds=300))) < timedelta(seconds=5)

    # Small drift between polls doesn't change the sensor.
    controller.get_combined_controller_state.return_value = controller_state(
        remaining=300 - 60 + 5, station=2
    )
    await _poll_core(hass, freezer)
    assert dt_util.parse_datetime(hass.states.get(REMAINING).state) == end

    # Busy controller: keep the last known end time.
    controller.get_combined_controller_state.side_effect = RainbirdDeviceBusyException()
    await _poll_core(hass, freezer)
    assert dt_util.parse_datetime(hass.states.get(REMAINING).state) == end


@pytest.mark.parametrize("active_zones", [{2}])
async def test_controller_without_remaining_support(
    hass: HomeAssistant,
    controller: MagicMock,
    setup_integrations: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Controllers that NACK the request are only asked once."""
    controller.get_combined_controller_state.side_effect = RainbirdDeviceNackError()
    await _poll_core(hass, freezer)
    await _poll_core(hass, freezer)
    assert controller.get_combined_controller_state.await_count == 2  # setup + 1
    assert hass.states.get(VALVE).state == "open"
    assert hass.states.get(REMAINING).state == STATE_UNKNOWN


async def test_busy_command(hass: HomeAssistant, setup_integrations: MagicMock) -> None:
    """A busy controller surfaces as a Home Assistant error."""
    setup_integrations.irrigate_zone.side_effect = RainbirdDeviceBusyException()
    with pytest.raises(HomeAssistantError, match="busy"):
        await hass.services.async_call(
            VALVE_DOMAIN, SERVICE_OPEN_VALVE, {ATTR_ENTITY_ID: VALVE}, blocking=True
        )
    assert hass.states.get(VALVE).state == "closed"


async def test_runtime_restored(
    hass: HomeAssistant,
    mock_rainbird: MagicMock,
    rainbird_entry: MockConfigEntry,
    extended_entry: MockConfigEntry,
) -> None:
    """The valve runtime survives restarts and is used when opening."""
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(RUNTIME, "900"),
                {
                    "native_max_value": 86400,
                    "native_min_value": 60,
                    "native_step": 60,
                    "native_unit_of_measurement": "s",
                    "native_value": 900,
                },
            )
        ],
    )
    assert await hass.config_entries.async_setup(rainbird_entry.entry_id)
    assert await hass.config_entries.async_setup(extended_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(RUNTIME).state == "900"
    await hass.services.async_call(
        VALVE_DOMAIN, SERVICE_OPEN_VALVE, {ATTR_ENTITY_ID: VALVE}, blocking=True
    )
    mock_rainbird.irrigate_zone.assert_awaited_once_with(2, 15)


async def test_waits_for_rainbird_and_follows_reload(
    hass: HomeAssistant,
    mock_rainbird: MagicMock,
    rainbird_entry: MockConfigEntry,
    extended_entry: MockConfigEntry,
) -> None:
    """Setup retries until Rain Bird is loaded and reloads with it."""
    with patch(
        "homeassistant.components.rainbird.create_controller",
        AsyncMock(side_effect=TimeoutError),
    ):
        await hass.config_entries.async_setup(extended_entry.entry_id)
        await hass.async_block_till_done()
    assert rainbird_entry.state is ConfigEntryState.SETUP_RETRY
    assert extended_entry.state is ConfigEntryState.SETUP_RETRY

    # Extended retries as soon as Rain Bird comes up, without its own backoff.
    await hass.config_entries.async_reload(rainbird_entry.entry_id)
    await hass.async_block_till_done()
    assert extended_entry.state is ConfigEntryState.LOADED

    await hass.config_entries.async_reload(rainbird_entry.entry_id)
    await hass.async_block_till_done()
    assert rainbird_entry.state is ConfigEntryState.LOADED
    assert extended_entry.state is ConfigEntryState.LOADED
    # Still wired to the new controller object after the reload.
    await hass.services.async_call(
        VALVE_DOMAIN, SERVICE_OPEN_VALVE, {ATTR_ENTITY_ID: VALVE}, blocking=True
    )
    mock_rainbird.irrigate_zone.assert_awaited_once_with(2, 6)
