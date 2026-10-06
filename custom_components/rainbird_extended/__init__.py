"""Rain Bird Extended: per-zone valves, run times and time remaining.

Adds entities to the zone devices created by the core Rain Bird integration,
reusing its connection to the controller.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import (
    SIGNAL_CONFIG_ENTRY_CHANGED,
    ConfigEntry,
    ConfigEntryChange,
    ConfigEntryState,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.typing import ConfigType

from .const import CONF_RAINBIRD_ENTRY_ID, DOMAIN, RAINBIRD_DOMAIN
from .coordinator import RainbirdExtendedCoordinator

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = [Platform.NUMBER, Platform.SENSOR, Platform.VALVE]

type RainbirdExtendedConfigEntry = ConfigEntry[RainbirdExtendedCoordinator]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Follow the core Rain Bird entries so we reload together with them."""

    @callback
    def _rainbird_entry_changed(change: ConfigEntryChange, entry: ConfigEntry) -> None:
        if (
            change is not ConfigEntryChange.UPDATED
            or entry.domain != RAINBIRD_DOMAIN
            or entry.state is not ConfigEntryState.LOADED
        ):
            return
        for ours in hass.config_entries.async_entries(DOMAIN):
            if ours.data.get(CONF_RAINBIRD_ENTRY_ID) != entry.entry_id:
                continue
            # Retry right away instead of waiting for the retry backoff, or
            # pick up the new controller objects after Rain Bird reloaded.
            if ours.state is ConfigEntryState.SETUP_RETRY or (
                ours.state is ConfigEntryState.LOADED
                and ours.runtime_data.rainbird_data is not entry.runtime_data
            ):
                hass.config_entries.async_schedule_reload(ours.entry_id)

    async_dispatcher_connect(hass, SIGNAL_CONFIG_ENTRY_CHANGED, _rainbird_entry_changed)
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: RainbirdExtendedConfigEntry
) -> bool:
    """Set up Rain Bird Extended on top of a loaded Rain Bird entry."""
    rainbird_entry = hass.config_entries.async_get_entry(
        entry.data[CONF_RAINBIRD_ENTRY_ID]
    )
    if rainbird_entry is None:
        raise ConfigEntryError(
            "The Rain Bird controller this extends was removed; "
            "delete and re-add Rain Bird Extended"
        )
    if rainbird_entry.state is not ConfigEntryState.LOADED:
        raise ConfigEntryNotReady(f"Waiting for Rain Bird ({rainbird_entry.title})")
    if rainbird_entry.unique_id is None:
        raise ConfigEntryError(
            "This Rain Bird controller has no unique ID, so its zone devices "
            "can't be extended"
        )

    coordinator = RainbirdExtendedCoordinator(hass, entry, rainbird_entry)
    await coordinator.async_refresh()
    entry.runtime_data = coordinator
    coordinator.async_start()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: RainbirdExtendedConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
