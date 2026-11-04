"""Rain Bird Extended: per-zone valves, run times and time remaining.

Adds its own controller and zone devices, linked to the ones the core Rain Bird
integration created. Commands and readings use Rain Bird's entities and
services. Rain Bird's entity registry is never written.
"""

from __future__ import annotations

from datetime import timedelta
import logging

import voluptuous as vol

from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN
from homeassistant.components.valve import DOMAIN as VALVE_DOMAIN
from homeassistant.config_entries import (
    SIGNAL_CONFIG_ENTRY_CHANGED,
    ConfigEntry,
    ConfigEntryChange,
    ConfigEntryState,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import (
    ConfigEntryError,
    ConfigEntryNotReady,
    ServiceValidationError,
)
from homeassistant.helpers import (
    config_validation as cv,
    entity_registry as er,
    issue_registry as ir,
    service,
)
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_CYCLE_AND_SOAK,
    ATTR_CYCLES,
    ATTR_DURATION,
    ATTR_ON_TIME,
    ATTR_REST,
    ATTR_ZONES,
    CONF_ADJUST_HIGH_PERCENT,
    CONF_ADJUST_HIGH_TEMPERATURE,
    CONF_ADJUST_LOW_PERCENT,
    CONF_ADJUST_LOW_TEMPERATURE,
    CONF_DISABLE_RAINBIRD_SWITCHES,
    CONF_RAINBIRD_ENTRY_ID,
    DOMAIN,
    RAINBIRD_DOMAIN,
    RUNTIME_MAX_SECONDS,
    RUNTIME_MIN_SECONDS,
    SERVICE_BLOWOUT,
    SERVICE_START_ZONE,
)
from .coordinator import RainbirdExtendedCoordinator
from .devices import async_follow_renames, async_sync_devices
from .entity import find_device
from .panel import async_register_panel, async_unregister_panel

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CALENDAR,
    Platform.EVENT,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.VALVE,
]

type RainbirdExtendedConfigEntry = ConfigEntry[RainbirdExtendedCoordinator]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Follow the core Rain Bird entries so we reload together with them."""

    @callback
    def _rainbird_entry_changed(change: ConfigEntryChange, entry: ConfigEntry) -> None:
        if entry.domain != RAINBIRD_DOMAIN:
            return
        if change is ConfigEntryChange.REMOVED:
            # Rain Bird was deleted: reload so setup raises the repair issue.
            for ours in hass.config_entries.async_entries(DOMAIN):
                if ours.data.get(CONF_RAINBIRD_ENTRY_ID) == entry.entry_id:
                    hass.config_entries.async_schedule_reload(ours.entry_id)
            return
        if (
            change is not ConfigEntryChange.UPDATED
            or entry.state is not ConfigEntryState.LOADED
        ):
            return
        for ours in hass.config_entries.async_entries(DOMAIN):
            if ours.data.get(CONF_RAINBIRD_ENTRY_ID) != entry.entry_id:
                continue
            # Retry right away instead of waiting for the retry backoff, or
            # pick up the new controller objects after Rain Bird reloaded.
            # Rain Bird finished loading, or its options changed. Retry
            # immediately, or reload so new zones and the duration option
            # are picked up. This does not look at Rain Bird's runtime data.
            if ours.state in (
                ConfigEntryState.SETUP_RETRY,
                ConfigEntryState.LOADED,
            ):
                hass.config_entries.async_schedule_reload(ours.entry_id)

    async_dispatcher_connect(hass, SIGNAL_CONFIG_ENTRY_CHANGED, _rainbird_entry_changed)

    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_START_ZONE,
        entity_domain=VALVE_DOMAIN,
        schema={
            vol.Required(ATTR_DURATION): vol.All(
                cv.time_period,
                vol.Range(
                    min=timedelta(seconds=RUNTIME_MIN_SECONDS),
                    max=timedelta(seconds=RUNTIME_MAX_SECONDS),
                ),
            ),
            vol.Optional(ATTR_CYCLE_AND_SOAK, default=False): cv.boolean,
        },
        func="async_start_zone",
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_BLOWOUT,
        entity_domain=BUTTON_DOMAIN,
        schema={
            vol.Optional(ATTR_ZONES): vol.All(
                cv.ensure_list, [vol.All(vol.Coerce(int), vol.Range(min=1))]
            ),
            vol.Optional(ATTR_CYCLES): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=50)
            ),
            vol.Optional(ATTR_ON_TIME): vol.All(
                cv.time_period,
                vol.Range(min=timedelta(minutes=1), max=timedelta(minutes=10)),
            ),
            vol.Optional(ATTR_REST): vol.All(
                cv.time_period,
                vol.Range(min=timedelta(0), max=timedelta(minutes=30)),
            ),
        },
        func=_async_blowout_service,
    )
    return True


async def _async_blowout_service(entity: Entity, call: ServiceCall) -> None:
    """Run rainbird_extended.blowout on the Blowout sprinklers button."""
    if not hasattr(entity, "async_blowout"):
        raise ServiceValidationError(
            f"{entity.entity_id} isn't a Rain Bird Extended Blowout sprinklers button"
        )
    await entity.async_blowout(
        zones=call.data.get(ATTR_ZONES),
        cycles=call.data.get(ATTR_CYCLES),
        on_time=call.data.get(ATTR_ON_TIME),
        rest=call.data.get(ATTR_REST),
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: RainbirdExtendedConfigEntry
) -> bool:
    """Set up Rain Bird Extended on top of a loaded Rain Bird entry."""
    rainbird_entry = hass.config_entries.async_get_entry(
        entry.data[CONF_RAINBIRD_ENTRY_ID]
    )
    if rainbird_entry is None:
        ir.async_create_issue(
            hass,
            DOMAIN,
            _issue_id(entry),
            is_fixable=True,
            is_persistent=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="rainbird_removed",
            translation_placeholders={"title": entry.title},
            data={"entry_id": entry.entry_id},
        )
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

    rainbird_device = find_device(
        hass, rainbird_entry.entry_id, str(rainbird_entry.unique_id)
    )
    if rainbird_device is None:
        raise ConfigEntryNotReady(
            f"Waiting for the Rain Bird controller device ({rainbird_entry.title})"
        )

    ir.async_delete_issue(hass, DOMAIN, _issue_id(entry))
    coordinator = RainbirdExtendedCoordinator(hass, entry, rainbird_entry)
    coordinator.linked_zones = coordinator.linkable_zones()
    # Our own devices, linked to Rain Bird's, before the entities join them.
    linked = async_sync_devices(hass, entry, rainbird_entry, coordinator.linked_zones)
    async_follow_renames(hass, entry, rainbird_entry, coordinator.linked_zones, linked)
    await coordinator.async_refresh()
    entry.runtime_data = coordinator
    coordinator.async_start()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_remove_stale_entities(hass, entry)
    await async_register_panel(hass)
    return True


# Options removed because they needed Rain Bird internals: disabling Rain
# Bird's switches, and weather adjustment (it wrote seasonal adjust).
_DROPPED_OPTIONS = frozenset(
    {
        CONF_DISABLE_RAINBIRD_SWITCHES,
        CONF_ADJUST_LOW_TEMPERATURE,
        CONF_ADJUST_LOW_PERCENT,
        CONF_ADJUST_HIGH_TEMPERATURE,
        CONF_ADJUST_HIGH_PERCENT,
    }
)


async def async_migrate_entry(
    hass: HomeAssistant, entry: RainbirdExtendedConfigEntry
) -> bool:
    """Drop options that wrote to Rain Bird or its registry.

    Switches Rain Bird owns are left exactly as they are: nothing is
    disabled or re-enabled here.
    """
    if entry.version > 2:
        return False
    if entry.version < 2:
        options = {
            key: value
            for key, value in entry.options.items()
            if key not in _DROPPED_OPTIONS
        }
        hass.config_entries.async_update_entry(entry, options=options, version=2)
        _LOGGER.debug("Migrated %s to version 2", entry.title)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: RainbirdExtendedConfigEntry
) -> bool:
    """Unload a config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await async_unregister_panel(hass, excluding_entry_id=entry.entry_id)
    return True


async def async_remove_entry(
    hass: HomeAssistant, entry: RainbirdExtendedConfigEntry
) -> None:
    """Clear our repair. Rain Bird's switches are not touched."""
    ir.async_delete_issue(hass, DOMAIN, _issue_id(entry))


def _issue_id(entry: ConfigEntry) -> str:
    return f"rainbird_removed_{entry.entry_id}"


# Entity keys removed in 2.0.0. They read or wrote the controller through
# Rain Bird internals, which a custom integration must not do.
_REMOVED_ENTITY_KEYS = ("seasonal_adjustment", "weather_adjustment")


@callback
def _async_remove_stale_entities(
    hass: HomeAssistant, entry: RainbirdExtendedConfigEntry
) -> None:
    """Remove entities this integration no longer creates.

    Only Rain Bird Extended's own registry entries are removed.
    """
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if any(key in entity.unique_id for key in _REMOVED_ENTITY_KEYS):
            _LOGGER.debug("Removing %s, no longer provided", entity.entity_id)
            registry.async_remove(entity.entity_id)
