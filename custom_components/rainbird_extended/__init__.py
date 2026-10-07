"""Rain Bird Extended: per-zone valves, run times and time remaining.

Adds entities to the zone devices created by the core Rain Bird integration,
reusing its connection to the controller.
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
    CONF_DISABLE_RAINBIRD_SWITCHES,
    CONF_RAINBIRD_ENTRY_ID,
    DEFAULT_DISABLE_RAINBIRD_SWITCHES,
    DOMAIN,
    RAINBIRD_DOMAIN,
    RUNTIME_MAX_SECONDS,
    RUNTIME_MIN_SECONDS,
    SERVICE_BLOWOUT,
    SERVICE_START_ZONE,
)
from .coordinator import RainbirdExtendedCoordinator

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
            if ours.state is ConfigEntryState.SETUP_RETRY or (
                ours.state is ConfigEntryState.LOADED
                and ours.runtime_data.rainbird_data is not entry.runtime_data
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

    ir.async_delete_issue(hass, DOMAIN, _issue_id(entry))
    coordinator = RainbirdExtendedCoordinator(hass, entry, rainbird_entry)
    coordinator.linked_zones = coordinator.linkable_zones()
    await coordinator.async_refresh()
    entry.runtime_data = coordinator
    coordinator.async_start()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_remove_stale_entities(hass, entry)

    _async_sync_rainbird_switches(
        hass,
        rainbird_entry,
        coordinator.linked_zones,
        disable=entry.options.get(
            CONF_DISABLE_RAINBIRD_SWITCHES, DEFAULT_DISABLE_RAINBIRD_SWITCHES
        ),
    )
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: RainbirdExtendedConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(
    hass: HomeAssistant, entry: RainbirdExtendedConfigEntry
) -> None:
    """Give the core Rain Bird switches back when this is removed."""
    ir.async_delete_issue(hass, DOMAIN, _issue_id(entry))
    if rainbird_entry := hass.config_entries.async_get_entry(
        entry.data[CONF_RAINBIRD_ENTRY_ID]
    ):
        _async_sync_rainbird_switches(hass, rainbird_entry, [], disable=False)


def _issue_id(entry: ConfigEntry) -> str:
    return f"rainbird_removed_{entry.entry_id}"


@callback
def _async_sync_rainbird_switches(
    hass: HomeAssistant,
    rainbird_entry: ConfigEntry,
    zones: list[int],
    *,
    disable: bool,
) -> None:
    """Disable (or re-enable) the core switches for zones that have a valve.

    Switches are marked as disabled by the integration, so ones the user
    disabled themselves are never re-enabled here.
    """
    registry = er.async_get(hass)
    uid = rainbird_entry.unique_id
    zone_uids = {f"{uid}-{zone}" for zone in zones}
    for entity in er.async_entries_for_config_entry(registry, rainbird_entry.entry_id):
        if entity.domain != Platform.SWITCH or not entity.unique_id.startswith(
            f"{uid}-"
        ):
            continue
        if disable and entity.unique_id in zone_uids:
            if entity.disabled_by is None:
                _LOGGER.debug("Disabling %s; use its valve instead", entity.entity_id)
                registry.async_update_entity(
                    entity.entity_id,
                    disabled_by=er.RegistryEntryDisabler.INTEGRATION,
                )
        elif entity.disabled_by is er.RegistryEntryDisabler.INTEGRATION:
            _LOGGER.debug("Re-enabling %s", entity.entity_id)
            registry.async_update_entity(entity.entity_id, disabled_by=None)


@callback
def _async_remove_stale_entities(
    hass: HomeAssistant, entry: RainbirdExtendedConfigEntry
) -> None:
    """Remove seasonal adjustment entities this controller no longer gets.

    Before 1.2.0 every controller had one Seasonal adjustment sensor from the
    controller state; controllers with water budgets now get one per program
    instead, which would otherwise leave the old one behind as unavailable.
    """
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if "seasonal_adjustment" not in entity.unique_id or entity.disabled_by:
            continue
        state = hass.states.get(entity.entity_id)
        if state is None or state.attributes.get("restored"):
            _LOGGER.debug("Removing %s, no longer provided", entity.entity_id)
            registry.async_remove(entity.entity_id)
