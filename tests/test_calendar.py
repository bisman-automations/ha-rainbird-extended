"""Tests for the run history calendar."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from homeassistant.components.valve import (
    DOMAIN as VALVE_DOMAIN,
    SERVICE_CLOSE_VALVE,
    SERVICE_OPEN_VALVE,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .helpers import _advance, _poll_core, _setup, _unload

CALENDAR = "calendar.rain_bird_controller_run_history"
VALVE = "valve.rain_bird_sprinkler_2"


async def _events(
    hass_client: ClientSessionGenerator, start: str, end: str
) -> list[dict[str, Any]]:
    client = await hass_client()
    response = await client.get(
        f"/api/calendars/{CALENDAR}", params={"start": start, "end": end}
    )
    assert response.status == 200
    return await response.json()


async def test_run_history(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    setup_integrations: MagicMock,
    active_zones: set[int],
    freezer: FrozenDateTimeFactory,
) -> None:
    """Running and finished runs show up with how long and what started them."""
    start = dt_util.utcnow().replace(microsecond=0)
    assert hass.states.get(CALENDAR).state == "off"
    await hass.services.async_call(
        VALVE_DOMAIN, SERVICE_OPEN_VALVE, {ATTR_ENTITY_ID: VALVE}, blocking=True
    )
    await hass.async_block_till_done()
    state = hass.states.get(CALENDAR)
    assert state.state == "on"
    assert state.attributes["message"] == "Rain Bird Sprinkler 2"
    assert state.attributes["description"] == "Running, started by Home Assistant"

    await _advance(hass, freezer, timedelta(minutes=2))
    await hass.services.async_call(
        VALVE_DOMAIN, SERVICE_CLOSE_VALVE, {ATTR_ENTITY_ID: VALVE}, blocking=True
    )
    await hass.async_block_till_done()
    state = hass.states.get(CALENDAR)
    assert state.state == "off"
    assert state.attributes["description"] == "2 min, started by Home Assistant"

    # A run started from the Rain Bird app.
    await _advance(hass, freezer, timedelta(minutes=10))
    active_zones.add(3)
    await _poll_core(hass, freezer)
    active_zones.clear()
    await _poll_core(hass, freezer)

    window_start = (start - timedelta(hours=1)).isoformat()
    window_end = (start + timedelta(hours=1)).isoformat()
    events = await _events(hass_client, window_start, window_end)
    assert [(e["summary"], e["description"]) for e in events] == [
        ("Rain Bird Sprinkler 2", "2 min, started by Home Assistant"),
        ("Rain Bird Sprinkler 3", "1 min, started by the controller or Rain Bird app"),
    ]
    # Nothing outside the window.
    later = (start + timedelta(days=1)).isoformat()
    assert await _events(hass_client, later, later[:10] + "T23:59:00+00:00") == []


async def test_run_history_survives_restart(
    hass: HomeAssistant,
    mock_rainbird: MagicMock,
    rainbird_entry: MockConfigEntry,
    extended_entry: MockConfigEntry,
    hass_storage: dict[str, Any],
) -> None:
    """Stored runs are loaded at startup."""
    start = dt_util.utcnow() - timedelta(hours=3)
    hass_storage[f"rainbird_extended.{extended_entry.entry_id}.run_history"] = {
        "version": 1,
        "data": {
            "runs": [
                {
                    "zone": 1,
                    "start": start.isoformat(),
                    "end": (start + timedelta(minutes=12)).isoformat(),
                    "source": "schedule",
                }
            ]
        },
    }
    await _setup(hass, rainbird_entry, extended_entry)
    state = hass.states.get("calendar.rain_bird_controller_run_history")
    assert state.attributes["message"] == "Rain Bird Sprinkler 1"
    assert state.attributes["description"] == "12 min, started by the schedule"
    await _unload(hass, rainbird_entry, extended_entry)
