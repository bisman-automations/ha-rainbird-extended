"""Config flow for Rain Bird Extended."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TimeSelector,
)

from .const import (
    CONF_BLOWOUT_CYCLES,
    CONF_BLOWOUT_ON_MINUTES,
    CONF_BLOWOUT_REST_SECONDS,
    CONF_BLOWOUT_ZONES,
    CONF_CYCLE_MINUTES,
    CONF_DISABLE_RAINBIRD_SWITCHES,
    CONF_RAIN_CHANCE,
    CONF_RAIN_CHECK_TIME,
    CONF_RAIN_DELAY_DAYS,
    CONF_RAINBIRD_ENTRY_ID,
    CONF_RUN_ALL_ZONES,
    CONF_SOAK_MINUTES,
    CONF_WEATHER_ENTITY,
    DEFAULT_BLOWOUT_CYCLES,
    DEFAULT_BLOWOUT_ON_MINUTES,
    DEFAULT_BLOWOUT_REST_SECONDS,
    DEFAULT_DISABLE_RAINBIRD_SWITCHES,
    DEFAULT_RAIN_CHANCE,
    DEFAULT_RAIN_CHECK_TIME,
    DEFAULT_RAIN_DELAY_DAYS,
    DEFAULT_SOAK_MINUTES,
    DOMAIN,
    RAINBIRD_DOMAIN,
)


class RainbirdExtendedConfigFlow(ConfigFlow, domain=DOMAIN):
    """Pick which Rain Bird controller to extend."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> RainbirdExtendedOptions:
        """Return the options flow."""
        return RainbirdExtendedOptions()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        configured = {
            entry.unique_id
            for entry in self._async_current_entries(include_ignore=False)
        }
        candidates = {
            entry.entry_id: entry.title
            for entry in self.hass.config_entries.async_entries(RAINBIRD_DOMAIN)
            if entry.entry_id not in configured
        }
        if not candidates:
            if configured:
                return self.async_abort(reason="already_configured")
            return self.async_abort(reason="no_rainbird")

        if user_input is not None:
            entry_id = user_input[CONF_RAINBIRD_ENTRY_ID]
            await self.async_set_unique_id(entry_id)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"{candidates[entry_id]} Extended",
                data={CONF_RAINBIRD_ENTRY_ID: entry_id},
            )

        if len(candidates) == 1:
            entry_id, title = next(iter(candidates.items()))
            await self.async_set_unique_id(entry_id)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"{title} Extended",
                data={CONF_RAINBIRD_ENTRY_ID: entry_id},
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_RAINBIRD_ENTRY_ID): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=entry_id, label=title)
                                for entry_id, title in candidates.items()
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )


class RainbirdExtendedOptions(OptionsFlowWithReload):
    """Rain Bird Extended options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}
        valves = zone_valves(self.hass, self.config_entry.entry_id)
        zone_of = {entity_id: zone for zone, entity_id in valves.items()}
        values: dict[str, Any] = dict(self.config_entry.options)
        if user_input is not None:
            values = dict(user_input)
            data = dict(user_input)
            for key in _ZONE_LIST_OPTIONS:
                picked = user_input.get(key) or []
                if any(entity_id not in zone_of for entity_id in picked):
                    errors[key] = "invalid_zones"
                    continue
                # Stored as zone numbers, in the order they were arranged.
                data[key] = list(dict.fromkeys(zone_of[e] for e in picked))
            if not errors:
                return self.async_create_entry(data=data)
        else:
            for key in _ZONE_LIST_OPTIONS:
                values[key] = [
                    valves[zone] for zone in values.get(key) or [] if zone in valves
                ]
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                _options_schema(list(valves.values())), values
            ),
            errors=errors,
        )


def zone_valves(hass: HomeAssistant, entry_id: str) -> dict[int, str]:
    """Each zone's valve entity, in zone order (zone number -> entity id)."""
    valves: dict[int, str] = {}
    for entry in er.async_entries_for_config_entry(er.async_get(hass), entry_id):
        if entry.domain != "valve" or entry.disabled_by is not None:
            continue
        # Zone valves have unique ids like "<controller>-<zone>-valve".
        _, zone, key = entry.unique_id.rsplit("-", 2)
        if key == "valve" and zone.isdigit():
            valves[int(zone)] = entry.entity_id
    return dict(sorted(valves.items()))


def _zone_picker(valves: list[str]) -> EntitySelector:
    """Pick zones by their valves and drag them into order."""
    return EntitySelector(
        EntitySelectorConfig(
            include_entities=valves, multiple=True, reorder=True, domain="valve"
        )
    )


_ZONE_LIST_OPTIONS = (CONF_RUN_ALL_ZONES, CONF_BLOWOUT_ZONES)

_OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required(
            CONF_DISABLE_RAINBIRD_SWITCHES, default=DEFAULT_DISABLE_RAINBIRD_SWITCHES
        ): bool,
        vol.Optional(CONF_RUN_ALL_ZONES, default=list): _zone_picker([]),
        vol.Required(CONF_CYCLE_MINUTES, default=0): NumberSelector(
            NumberSelectorConfig(
                min=0,
                max=60,
                step=1,
                unit_of_measurement="min",
                mode=NumberSelectorMode.BOX,
            )
        ),
        vol.Required(CONF_SOAK_MINUTES, default=DEFAULT_SOAK_MINUTES): NumberSelector(
            NumberSelectorConfig(
                min=0,
                max=240,
                step=1,
                unit_of_measurement="min",
                mode=NumberSelectorMode.BOX,
            )
        ),
        vol.Optional(CONF_BLOWOUT_ZONES, default=list): _zone_picker([]),
        vol.Required(
            CONF_BLOWOUT_CYCLES, default=DEFAULT_BLOWOUT_CYCLES
        ): NumberSelector(
            NumberSelectorConfig(min=1, max=50, step=1, mode=NumberSelectorMode.BOX)
        ),
        vol.Required(
            CONF_BLOWOUT_ON_MINUTES, default=DEFAULT_BLOWOUT_ON_MINUTES
        ): NumberSelector(
            NumberSelectorConfig(
                min=1,
                max=10,
                step=1,
                unit_of_measurement="min",
                mode=NumberSelectorMode.BOX,
            )
        ),
        vol.Required(
            CONF_BLOWOUT_REST_SECONDS, default=DEFAULT_BLOWOUT_REST_SECONDS
        ): NumberSelector(
            NumberSelectorConfig(
                min=0,
                max=1800,
                step=15,
                unit_of_measurement="s",
                mode=NumberSelectorMode.BOX,
            )
        ),
        vol.Optional(CONF_WEATHER_ENTITY): EntitySelector(
            EntitySelectorConfig(domain="weather")
        ),
        vol.Required(CONF_RAIN_CHANCE, default=DEFAULT_RAIN_CHANCE): NumberSelector(
            NumberSelectorConfig(
                min=10,
                max=100,
                step=5,
                unit_of_measurement="%",
                mode=NumberSelectorMode.SLIDER,
            )
        ),
        vol.Required(
            CONF_RAIN_CHECK_TIME, default=DEFAULT_RAIN_CHECK_TIME
        ): TimeSelector(),
        vol.Required(
            CONF_RAIN_DELAY_DAYS, default=DEFAULT_RAIN_DELAY_DAYS
        ): NumberSelector(
            NumberSelectorConfig(min=1, max=14, step=1, mode=NumberSelectorMode.BOX)
        ),
    }
)


def _options_schema(valves: list[str]) -> vol.Schema:
    """The options, with the zone pickers limited to this controller's zones."""
    picker = _zone_picker(valves)
    return vol.Schema(
        {
            (
                vol.Optional(key.schema, default=list)
                if key in _ZONE_LIST_OPTIONS
                else key
            ): (picker if key in _ZONE_LIST_OPTIONS else value)
            for key, value in _OPTIONS_SCHEMA.schema.items()
        }
    )
