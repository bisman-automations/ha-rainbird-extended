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
    TimeSelector,
)

from .const import (
    CONF_DISABLE_RAINBIRD_SWITCHES,
    CONF_RAIN_CHANCE,
    CONF_RAIN_CHECK_TIME,
    CONF_RAIN_DELAY_DAYS,
    CONF_RAINBIRD_ENTRY_ID,
    CONF_WEATHER_ENTITY,
    DEFAULT_DISABLE_RAINBIRD_SWITCHES,
    DEFAULT_RAIN_CHANCE,
    DEFAULT_RAIN_CHECK_TIME,
    DEFAULT_RAIN_DELAY_DAYS,
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
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_DISABLE_RAINBIRD_SWITCHES,
                        default=options.get(
                            CONF_DISABLE_RAINBIRD_SWITCHES,
                            DEFAULT_DISABLE_RAINBIRD_SWITCHES,
                        ),
                    ): bool,
                    vol.Optional(
                        CONF_WEATHER_ENTITY,
                        description={
                            "suggested_value": options.get(CONF_WEATHER_ENTITY)
                        },
                    ): EntitySelector(EntitySelectorConfig(domain="weather")),
                    vol.Required(
                        CONF_RAIN_CHANCE,
                        default=options.get(CONF_RAIN_CHANCE, DEFAULT_RAIN_CHANCE),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=10,
                            max=100,
                            step=5,
                            unit_of_measurement="%",
                            mode=NumberSelectorMode.SLIDER,
                        )
                    ),
                    vol.Required(
                        CONF_RAIN_CHECK_TIME,
                        default=options.get(
                            CONF_RAIN_CHECK_TIME, DEFAULT_RAIN_CHECK_TIME
                        ),
                    ): TimeSelector(),
                    vol.Required(
                        CONF_RAIN_DELAY_DAYS,
                        default=options.get(
                            CONF_RAIN_DELAY_DAYS, DEFAULT_RAIN_DELAY_DAYS
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1, max=14, step=1, mode=NumberSelectorMode.BOX
                        )
                    ),
                }
            ),
        )
