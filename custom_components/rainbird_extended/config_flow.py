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
    DateSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
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
from .programs import (
    CONF_DAYS,
    CONF_EVERY_DAYS,
    CONF_FREQUENCY,
    CONF_PROGRAM_NAME,
    CONF_SEASONAL_ADJUST,
    CONF_START_DATE,
    CONF_START_TIMES,
    CONF_STATION_DELAY,
    CONF_ZONES,
    DEFAULT_EVERY_DAYS,
    DEFAULT_SEASONAL_ADJUST,
    EVERY_DAYS_MAX,
    FREQUENCIES,
    FREQUENCY_CUSTOM,
    STATION_DELAY_MAX,
    WEEKDAYS,
    program_key,
    program_letter,
)
from .weather import default_unit

# Programs that can be set up in the options (A to D).
MAX_PROGRAMS = 4


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
    """Rain Bird Extended options, shown in sections and stored flat.

    Controllers with programs get a menu: the settings, and a form for each
    program (stored as one dict per program).
    """

    def _program_count(self) -> int:
        coordinator = getattr(self.config_entry, "runtime_data", None)
        return min(getattr(coordinator, "max_programs", 0) or 0, MAX_PROGRAMS)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick what to change."""
        if not (count := self._program_count()):
            return await self.async_step_settings(user_input)
        return self.async_show_menu(
            step_id="init",
            menu_options=["settings", *(program_key(i) for i in range(count))],
        )

    async def async_step_program_a(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Program A."""
        return await self._async_program(0, user_input)

    async def async_step_program_b(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Program B."""
        return await self._async_program(1, user_input)

    async def async_step_program_c(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Program C."""
        return await self._async_program(2, user_input)

    async def async_step_program_d(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Program D."""
        return await self._async_program(3, user_input)

    async def _async_program(
        self, program: int, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        """A program's schedule, as set in the Rain Bird app."""
        errors: dict[str, str] = {}
        key = program_key(program)
        valves = zone_valves(self.hass, self.config_entry.entry_id)
        zone_of = {entity_id: zone for zone, entity_id in valves.items()}
        schema = program_schema(list(valves.values()))
        if user_input is not None:
            data = dict(user_input)
            picked = data.get(CONF_ZONES) or []
            if any(entity_id not in zone_of for entity_id in picked):
                errors["base"] = "invalid_zones"
            elif data.get(CONF_FREQUENCY) == FREQUENCY_CUSTOM and not data.get(
                CONF_DAYS
            ):
                errors[CONF_DAYS] = "no_days"
            else:
                data[CONF_ZONES] = sorted({zone_of[e] for e in picked})
                return self.async_create_entry(
                    data={**self.config_entry.options, key: data}
                )
            values = user_input
        else:
            values = dict(self.config_entry.options.get(key) or {})
            values[CONF_ZONES] = [
                valves[zone] for zone in values.get(CONF_ZONES) or [] if zone in valves
            ]
        return self.async_show_form(
            step_id=key,
            data_schema=self.add_suggested_values_to_schema(schema, values),
            errors=errors,
            description_placeholders={"program": program_letter(program)},
        )

    async def async_step_settings(
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
                # Keep the programs, which have forms of their own.
                programs = {
                    key: value
                    for key, value in self.config_entry.options.items()
                    if key.startswith("program_")
                }
                return self.async_create_entry(data={**programs, **data})
            values = user_input
        else:
            options = {
                key: value
                for key, value in options.items()
                if not key.startswith("program_")
            }
            for key in _ZONE_LIST_OPTIONS:
                options[key] = [
                    valves[zone] for zone in options.get(key) or [] if zone in valves
                ]
            values = nest(options)
        return self.async_show_form(
            step_id="settings",
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


def program_schema(valves: list[str]) -> vol.Schema:
    """A program's form, laid out like the Rain Bird app."""
    return vol.Schema(
        {
            vol.Optional(CONF_PROGRAM_NAME): TextSelector(),
            vol.Required(CONF_FREQUENCY, default=FREQUENCY_CUSTOM): SelectSelector(
                SelectSelectorConfig(
                    options=list(FREQUENCIES),
                    mode=SelectSelectorMode.LIST,
                    translation_key="frequency",
                )
            ),
            vol.Optional(CONF_DAYS, default=list(WEEKDAYS)): SelectSelector(
                SelectSelectorConfig(
                    options=list(WEEKDAYS),
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                    translation_key="weekday",
                )
            ),
            vol.Required(CONF_EVERY_DAYS, default=DEFAULT_EVERY_DAYS): _number(
                1, EVERY_DAYS_MAX, 1, "days"
            ),
            vol.Optional(CONF_START_DATE): DateSelector(),
            **{vol.Optional(key): TimeSelector() for key in CONF_START_TIMES},
            vol.Optional(CONF_ZONES, default=list): EntitySelector(
                EntitySelectorConfig(
                    include_entities=valves, multiple=True, domain="valve"
                )
            ),
            vol.Required(CONF_STATION_DELAY, default=0): _number(
                0, STATION_DELAY_MAX, 1, "s"
            ),
            vol.Required(
                CONF_SEASONAL_ADJUST, default=DEFAULT_SEASONAL_ADJUST
            ): _number(5, 200, 1, "%"),
        }
    )


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
