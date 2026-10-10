"""Helpers shared by the tests."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult, FlowResultType
from homeassistant.util import dt as dt_util


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
