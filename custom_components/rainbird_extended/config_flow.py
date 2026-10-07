"""Config flow for Rain Bird Extended."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
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
    TextSelector,
    TimeSelector,
)

from .const import (
    CONF_CYCLE_MINUTES,
    CONF_DISABLE_RAINBIRD_SWITCHES,
    CONF_RAIN_CHANCE,
    CONF_RAIN_CHECK_TIME,
    CONF_RAIN_DELAY_DAYS,
    CONF_RAINBIRD_ENTRY_ID,
    CONF_RUN_ALL_ZONES,
    CONF_SOAK_MINUTES,
    CONF_WEATHER_ENTITY,
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
        values: dict[str, Any] = dict(self.config_entry.options)
        if user_input is not None:
            values = dict(user_input)
            try:
                zones = _parse_zones(
                    user_input.get(CONF_RUN_ALL_ZONES, ""), self._known_zones()
                )
            except ValueError:
                errors[CONF_RUN_ALL_ZONES] = "invalid_zones"
            else:
                data = dict(user_input)
                data[CONF_RUN_ALL_ZONES] = zones
                return self.async_create_entry(data=data)
        elif zones := values.get(CONF_RUN_ALL_ZONES):
            values[CONF_RUN_ALL_ZONES] = ", ".join(str(zone) for zone in zones)
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(_OPTIONS_SCHEMA, values),
            errors=errors,
        )

    def _known_zones(self) -> set[int] | None:
        if self.config_entry.state is ConfigEntryState.LOADED:
            return self.config_entry.runtime_data.zones
        return None


def _parse_zones(text: str, known: set[int] | None) -> list[int]:
    """Parse "3, 1, 2" into [3, 1, 2]; empty means every zone."""
    parts = [part.strip() for part in text.replace(";", ",").split(",")]
    zones = [int(part) for part in parts if part]
    if len(set(zones)) != len(zones) or any(zone < 1 for zone in zones):
        raise ValueError
    if known is not None and any(zone not in known for zone in zones):
        raise ValueError
    return zones


_OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required(
            CONF_DISABLE_RAINBIRD_SWITCHES, default=DEFAULT_DISABLE_RAINBIRD_SWITCHES
        ): bool,
        vol.Optional(CONF_RUN_ALL_ZONES, default=""): TextSelector(),
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
