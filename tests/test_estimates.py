"""Tests for the estimated next run and time remaining while idle."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache,
)

from homeassistant.components.number import (
    ATTR_VALUE,
    DOMAIN as NUMBER_DOMAIN,
    SERVICE_SET_VALUE,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util

from .helpers import _advance, _poll_core, _setup, _unload

# Zone 3 isn't in the test controller's schedule, so it's estimated.
NEXT_RUN = "sensor.rain_bird_sprinkler_3_estimated_next_run"
REMAINING = "sensor.rain_bird_sprinkler_3_time_remaining"
INTERVAL = "number.rain_bird_sprinkler_3_run_every"


def _next(hass: HomeAssistant) -> object:
    return dt_util.parse_datetime(hass.states.get(NEXT_RUN).state)


# Away from the test schedule (05:00-05:25 local).
@pytest.mark.freeze_time("2026-10-09 20:00:00+00:00")
async def test_next_run_from_last_run_and_interval(
    hass: HomeAssistant,
    setup_integrations: MagicMock,
    active_zones: set[int],
    freezer: FrozenDateTimeFactory,
) -> None:
    """Last start + Run every; moved on past missed days; follows the interval."""
    assert hass.states.get(INTERVAL).state == "1"
    assert hass.states.get(INTERVAL).attributes["unit_of_measurement"] == "d"

    # Zone 3 runs (started outside Home Assistant) at about 20:01.
    active_zones.add(3)
    await _poll_core(hass, freezer)
    started = dt_util.parse_datetime(hass.states.get(NEXT_RUN).attributes["last_start"])
    assert _next(hass) == started + timedelta(days=1)
    active_zones.clear()
    await _poll_core(hass, freezer)

    # Every 3 days.
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: INTERVAL, ATTR_VALUE: 3},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert _next(hass) == started + timedelta(days=3)

    # It didn't run then (rain, say): the next one after that.
    await _advance(hass, freezer, timedelta(days=4))
    await _poll_core(hass, freezer)
    assert _next(hass) == started + timedelta(days=6)
    assert hass.states.get(NEXT_RUN).attributes["estimated"] is True

    # Idle, time remaining shows when the run ended.
    ended = dt_util.parse_datetime(hass.states.get(REMAINING).state)
    assert started < ended < started + timedelta(minutes=5)


async def test_restored_after_restart(
    hass: HomeAssistant,
    mock_rainbird: MagicMock,
    rainbird_entry: MockConfigEntry,
    extended_entry: MockConfigEntry,
) -> None:
    """The last run's end and the start the estimate counts from survive."""
    last_start = (dt_util.utcnow() - timedelta(hours=30)).replace(microsecond=0)
    last_end = last_start + timedelta(minutes=8)
    mock_restore_cache(
        hass,
        [
            State(REMAINING, last_end.isoformat()),
            State(
                NEXT_RUN,
                (last_start + timedelta(days=2)).isoformat(),
                {"last_start": last_start.isoformat(), "estimated": True},
            ),
            State(INTERVAL, "2"),
        ],
    )
    await _setup(hass, rainbird_entry, extended_entry)
    assert dt_util.parse_datetime(hass.states.get(REMAINING).state) == last_end
    assert _next(hass) == last_start + timedelta(days=2)
    await _unload(hass, rainbird_entry, extended_entry)


async def test_never_ran_counts_from_first_use(
    hass: HomeAssistant, setup_integrations: MagicMock
) -> None:
    """Before any run, the estimate counts from when it was first needed."""
    state = hass.states.get(NEXT_RUN)
    start = dt_util.parse_datetime(state.attributes["last_start"])
    assert _next(hass) == start + timedelta(days=1)
