"""Helpers shared by the tests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
import json
from typing import Any
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, State
from homeassistant.data_entry_flow import FlowResult, FlowResultType
from homeassistant.helpers import entity_registry as er, restore_state as rs
from homeassistant.helpers.json import JSONEncoder
from homeassistant.util import dt as dt_util

from .conftest import RAINBIRD_UNIQUE_ID


async def _poll_core(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Advance a minute and have the core Rain Bird coordinator poll again."""
    freezer.tick(timedelta(minutes=1))
    async_fire_time_changed(hass)
    for entry in hass.config_entries.async_entries("rainbird"):
        await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
    # Let our own refresh debounce run.
    freezer.tick(timedelta(seconds=3))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def _advance(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta: timedelta
) -> None:
    """Move the clock forward and run whatever is due."""
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def _local(hour: int, minute: int, second: int = 0) -> datetime:
    """Today at a local time."""
    return dt_util.now().replace(hour=hour, minute=minute, second=second, microsecond=0)


def zone_calendar_events(
    description: str,
    *,
    hour: int = 5,
    minute: int = 0,
    length: timedelta = timedelta(minutes=25),
    summary: str = "PGM A",
) -> list[dict[str, str]]:
    """Rain Bird calendar events that list zone run times, as in HA 2026.11+.

    One occurrence a day from yesterday through the next week, so a test can
    sit inside today's run or look ahead to the next one.
    """
    today = _local(hour, minute)
    events = []
    for delta in range(-1, 8):
        start = today + timedelta(days=delta)
        events.append(
            {
                "summary": summary,
                "start": start.isoformat(),
                "end": (start + length).isoformat(),
                "description": description,
            }
        )
    return events


async def _setup(hass: HomeAssistant, *entries: MockConfigEntry) -> None:
    """Set up config entries in order."""
    for entry in entries:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def _unload(hass: HomeAssistant, *entries: MockConfigEntry) -> None:
    """Unload config entries in reverse order."""
    for entry in reversed(entries):
        await hass.config_entries.async_unload(entry.entry_id)


def _calls(controller: MagicMock) -> list[tuple[int, int]]:
    """The (zone, minutes) of every irrigate_zone call."""
    return [call.args for call in controller.irrigate_zone.await_args_list]


async def _press(hass: HomeAssistant, entity_id: str) -> None:
    """Press a button."""
    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )
    await hass.async_block_till_done()


async def _open_options(
    hass: HomeAssistant, entry_id: str, step: str = "settings"
) -> FlowResult:
    """Open the options and pick a step from the menu."""
    result = await hass.config_entries.options.async_init(entry_id)
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step}
    )


def restore_states(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    states: Sequence[tuple[State, str, Mapping[str, Any] | None]],
) -> None:
    """Give our entities a saved state from before a restart.

    Each item is the saved state, the end of the entity's unique id after the
    controller ("3-time_remaining") and the extra data, if any. The entities
    are registered first, as they are after a real restart: Home Assistant
    2026.11 keeps saved states by entity registry id, older releases by
    entity id.
    """
    registry = er.async_get(hass)
    stored: list[dict[str, Any]] = []
    now = dt_util.utcnow()
    for state, key, extra_data in states:
        domain, object_id = state.entity_id.split(".", 1)
        entity = registry.async_get_or_create(
            domain,
            "rainbird_extended",
            f"{RAINBIRD_UNIQUE_ID}-{key}",
            config_entry=entry,
            suggested_object_id=object_id,
        )
        assert entity.entity_id == state.entity_id
        saved = dict(state.as_dict())
        saved["attributes"] = json.loads(
            json.dumps(saved["attributes"], cls=JSONEncoder)
        )
        stored.append({"state": saved, "extra_data": extra_data, "last_seen": now})
    data = rs.RestoreStateData(hass)
    if hasattr(data, "_async_load_stored_states"):  # Home Assistant 2026.11+
        data._async_load_stored_states(stored)
    else:
        data.last_states = {
            item["state"]["entity_id"]: rs.StoredState.from_dict(item)
            for item in stored
        }
    rs.async_get.cache_clear()
    hass.data[rs.DATA_RESTORE_STATE] = data
