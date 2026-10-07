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
from homeassistant.data_entry_flow import section
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
    CONF_ADJUST_HIGH_PERCENT,
    CONF_ADJUST_HIGH_TEMPERATURE,
    CONF_ADJUST_LOW_PERCENT,
    CONF_ADJUST_LOW_TEMPERATURE,
    CONF_BLOWOUT_CYCLES,
    CONF_BLOWOUT_ON_MINUTES,
    CONF_BLOWOUT_REST_SECONDS,
    CONF_BLOWOUT_ZONES,
    CONF_CYCLE_MINUTES,
    CONF_DISABLE_RAINBIRD_SWITCHES,
    CONF_FREEZE_DELAY_DAYS,
    CONF_FREEZE_SENSOR,
    CONF_FREEZE_TEMPERATURE,
    CONF_MOISTURE_DELAY_DAYS,
    CONF_MOISTURE_SENSOR,
    CONF_MOISTURE_THRESHOLD,
    CONF_RAIN_CHANCE,
    CONF_RAIN_CHECK_TIME,
    CONF_RAIN_DELAY_DAYS,
    CONF_RAINBIRD_ENTRY_ID,
    CONF_RUN_ALL_ZONES,
    CONF_SOAK_MINUTES,
    CONF_TEMPERATURE_UNIT,
    CONF_WEATHER_ENTITY,
    DEFAULT_ADJUST_HIGH_PERCENT,
    DEFAULT_ADJUST_LOW_PERCENT,
    DEFAULT_BLOWOUT_CYCLES,
    DEFAULT_BLOWOUT_ON_MINUTES,
    DEFAULT_BLOWOUT_REST_SECONDS,
    DEFAULT_DISABLE_RAINBIRD_SWITCHES,
    DEFAULT_FREEZE_DELAY_DAYS,
    DEFAULT_MOISTURE_DELAY_DAYS,
    DEFAULT_MOISTURE_THRESHOLD,
    DEFAULT_RAIN_CHANCE,
    DEFAULT_RAIN_CHECK_TIME,
    DEFAULT_RAIN_DELAY_DAYS,
    DEFAULT_SOAK_MINUTES,
    DOMAIN,
    RAINBIRD_DOMAIN,
    SECTION_BLOWOUT,
    SECTION_FREEZE_SKIP,
    SECTION_MOISTURE_SKIP,
    SECTION_RAIN_SKIP,
    SECTION_RUN_ALL_ZONES,
    SECTION_WEATHER,
    SECTION_WEATHER_ADJUSTMENT,
    TEMPERATURE_DEFAULTS,
)
from .weather import default_unit


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
    """Rain Bird Extended options, shown in sections and stored flat."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}
        valves = zone_valves(self.hass, self.config_entry.entry_id)
        zone_of = {entity_id: zone for zone, entity_id in valves.items()}
        options = dict(self.config_entry.options)
        unit = options.get(CONF_TEMPERATURE_UNIT) or default_unit(self.hass)
        schema = options_schema(list(valves.values()), unit)
        if user_input is not None:
            data = flatten(user_input)
            for key in _ZONE_LIST_OPTIONS:
                picked = data.get(key) or []
                if any(entity_id not in zone_of for entity_id in picked):
                    errors["base"] = "invalid_zones"
                    continue
                # Stored as zone numbers, in the order they were arranged.
                data[key] = list(dict.fromkeys(zone_of[e] for e in picked))
            if not errors:
                data[CONF_TEMPERATURE_UNIT] = unit
                return self.async_create_entry(data=data)
            values = user_input
        else:
            for key in _ZONE_LIST_OPTIONS:
                options[key] = [
                    valves[zone] for zone in options.get(key) or [] if zone in valves
                ]
            values = nest(options)
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(schema, values),
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


def _number(
    minimum: float, maximum: float, step: float, unit: str | None = None
) -> NumberSelector:
    config = NumberSelectorConfig(
        min=minimum, max=maximum, step=step, mode=NumberSelectorMode.BOX
    )
    if unit:
        config["unit_of_measurement"] = unit
    return NumberSelector(config)


_ZONE_LIST_OPTIONS = (CONF_RUN_ALL_ZONES, CONF_BLOWOUT_ZONES)


def options_schema(valves: list[str], unit: str) -> vol.Schema:
    """The options form, with zone pickers limited to this controller's zones."""
    temps = TEMPERATURE_DEFAULTS.get(unit, TEMPERATURE_DEFAULTS["°C"])
    picker = _zone_picker(valves)
    sections: dict[str, dict[Any, Any]] = {
        SECTION_RUN_ALL_ZONES: {
            vol.Optional(CONF_RUN_ALL_ZONES, default=list): picker,
            vol.Required(CONF_CYCLE_MINUTES, default=0): _number(0, 60, 1, "min"),
            vol.Required(CONF_SOAK_MINUTES, default=DEFAULT_SOAK_MINUTES): _number(
                0, 240, 1, "min"
            ),
        },
        SECTION_BLOWOUT: {
            vol.Optional(CONF_BLOWOUT_ZONES, default=list): picker,
            vol.Required(CONF_BLOWOUT_CYCLES, default=DEFAULT_BLOWOUT_CYCLES): _number(
                1, 50, 1
            ),
            vol.Required(
                CONF_BLOWOUT_ON_MINUTES, default=DEFAULT_BLOWOUT_ON_MINUTES
            ): _number(1, 10, 1, "min"),
            vol.Required(
                CONF_BLOWOUT_REST_SECONDS, default=DEFAULT_BLOWOUT_REST_SECONDS
            ): _number(0, 1800, 15, "s"),
        },
        SECTION_WEATHER: {
            vol.Optional(CONF_WEATHER_ENTITY): EntitySelector(
                EntitySelectorConfig(domain="weather")
            ),
            vol.Required(
                CONF_RAIN_CHECK_TIME, default=DEFAULT_RAIN_CHECK_TIME
            ): TimeSelector(),
        },
        SECTION_RAIN_SKIP: {
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
                CONF_RAIN_DELAY_DAYS, default=DEFAULT_RAIN_DELAY_DAYS
            ): _number(1, 14, 1),
        },
        SECTION_FREEZE_SKIP: {
            vol.Required(
                CONF_FREEZE_TEMPERATURE, default=temps[CONF_FREEZE_TEMPERATURE]
            ): _number(-20, 60, 0.5, unit),
            vol.Optional(CONF_FREEZE_SENSOR): EntitySelector(
                EntitySelectorConfig(domain="sensor", device_class="temperature")
            ),
            vol.Required(
                CONF_FREEZE_DELAY_DAYS, default=DEFAULT_FREEZE_DELAY_DAYS
            ): _number(1, 14, 1),
        },
        SECTION_MOISTURE_SKIP: {
            vol.Optional(CONF_MOISTURE_SENSOR): EntitySelector(
                EntitySelectorConfig(domain="sensor", device_class="moisture")
            ),
            vol.Required(
                CONF_MOISTURE_THRESHOLD, default=DEFAULT_MOISTURE_THRESHOLD
            ): _number(1, 100, 1, "%"),
            vol.Required(
                CONF_MOISTURE_DELAY_DAYS, default=DEFAULT_MOISTURE_DELAY_DAYS
            ): _number(1, 14, 1),
        },
        SECTION_WEATHER_ADJUSTMENT: {
            vol.Required(
                CONF_ADJUST_LOW_TEMPERATURE, default=temps[CONF_ADJUST_LOW_TEMPERATURE]
            ): _number(-20, 130, 1, unit),
            vol.Required(
                CONF_ADJUST_LOW_PERCENT, default=DEFAULT_ADJUST_LOW_PERCENT
            ): _number(10, 200, 5, "%"),
            vol.Required(
                CONF_ADJUST_HIGH_TEMPERATURE,
                default=temps[CONF_ADJUST_HIGH_TEMPERATURE],
            ): _number(-20, 130, 1, unit),
            vol.Required(
                CONF_ADJUST_HIGH_PERCENT, default=DEFAULT_ADJUST_HIGH_PERCENT
            ): _number(10, 200, 5, "%"),
        },
    }
    return vol.Schema(
        {
            vol.Required(
                CONF_DISABLE_RAINBIRD_SWITCHES,
                default=DEFAULT_DISABLE_RAINBIRD_SWITCHES,
            ): bool,
            **{
                vol.Required(name): section(
                    vol.Schema(fields), {"collapsed": name != SECTION_WEATHER}
                )
                for name, fields in sections.items()
            },
        }
    )


# Which section each option is shown in (options are stored flat).
_SECTION_OF: dict[str, str] = {
    str(key.schema): name
    for name, fields in options_schema([], "°C").schema.items()
    if isinstance(fields, section)
    for key in fields.schema.schema
}


def flatten(user_input: dict[str, Any]) -> dict[str, Any]:
    """Options as submitted (with sections) to how they're stored."""
    data: dict[str, Any] = {}
    for key, value in user_input.items():
        if key in _SECTIONS and isinstance(value, dict):
            data.update(value)
        else:
            data[key] = value
    return data


def nest(options: dict[str, Any]) -> dict[str, Any]:
    """Stored options to the form's sections."""
    values: dict[str, Any] = {name: {} for name in _SECTIONS}
    for key, value in options.items():
        if name := _SECTION_OF.get(key):
            values[name][key] = value
        else:
            values[key] = value
    return values


_SECTIONS = set(_SECTION_OF.values())
