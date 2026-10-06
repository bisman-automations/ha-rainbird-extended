"""Rain skip: set a rain delay when rain is forecast."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_OFF
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from . import RainbirdExtendedConfigEntry
from .const import (
    CONF_RAIN_CHANCE,
    CONF_RAIN_CHECK_TIME,
    CONF_RAIN_DELAY_DAYS,
    CONF_WEATHER_ENTITY,
    DEFAULT_RAIN_CHANCE,
    DEFAULT_RAIN_CHECK_TIME,
    DEFAULT_RAIN_DELAY_DAYS,
    EVENT_RAIN_SKIP,
    RAINY_CONDITIONS,
)
from .coordinator import RainbirdExtendedCoordinator
from .entity import RainbirdExtendedControllerEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RainbirdExtendedConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the rain skip switch when a weather entity is configured."""
    if entry.options.get(CONF_WEATHER_ENTITY):
        async_add_entities([RainbirdRainSkip(entry.runtime_data, entry.options)])


class RainbirdRainSkip(RainbirdExtendedControllerEntity, SwitchEntity, RestoreEntity):
    """While on, check the forecast each day and set a rain delay if wet."""

    _attr_translation_key = "rain_skip"

    def __init__(
        self, coordinator: RainbirdExtendedCoordinator, options: dict[str, Any]
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, "rain_skip")
        self._weather: str = options[CONF_WEATHER_ENTITY]
        self._threshold = int(options.get(CONF_RAIN_CHANCE, DEFAULT_RAIN_CHANCE))
        self._days = int(options.get(CONF_RAIN_DELAY_DAYS, DEFAULT_RAIN_DELAY_DAYS))
        check = dt_util.parse_time(
            options.get(CONF_RAIN_CHECK_TIME, DEFAULT_RAIN_CHECK_TIME)
        )
        self._check = check or dt_util.parse_time(DEFAULT_RAIN_CHECK_TIME)
        self._attr_is_on = True
        self._last_check: datetime | None = None
        self._last_chance: int | None = None
        self._last_skipped: datetime | None = None

    async def async_added_to_hass(self) -> None:
        """Restore on/off and schedule the daily check."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            self._attr_is_on = last.state != STATE_OFF
            if skipped := last.attributes.get("last_skipped"):
                self._last_skipped = dt_util.parse_datetime(skipped)
        assert self._check is not None
        self.async_on_remove(
            async_track_time_change(
                self.hass,
                self._async_scheduled_check,
                hour=self._check.hour,
                minute=self._check.minute,
                second=self._check.second,
            )
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return what the last check found."""
        return {
            "weather_entity": self._weather,
            "rain_chance_threshold": self._threshold,
            "check_time": self._check.isoformat() if self._check else None,
            "last_check": self._last_check.isoformat() if self._last_check else None,
            "last_rain_chance": self._last_chance,
            "last_skipped": (
                self._last_skipped.isoformat() if self._last_skipped else None
            ),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn rain skip on."""
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn rain skip off."""
        self._attr_is_on = False
        self.async_write_ha_state()

    @callback
    def _async_scheduled_check(self, _now: datetime) -> None:
        if self.is_on:
            self.coordinator.config_entry.async_create_background_task(
                self.hass, self.async_check_forecast(), "rain skip check"
            )

    async def async_check_forecast(self) -> None:
        """Set a rain delay if today's forecast is wet enough."""
        try:
            chance, condition = await self._async_todays_forecast()
        except HomeAssistantError as err:
            _LOGGER.warning("Rain skip: could not get the forecast: %s", err)
            return
        self._last_check = dt_util.utcnow()
        self._last_chance = chance
        wet = (chance is not None and chance >= self._threshold) or (
            chance is None and condition in RAINY_CONDITIONS
        )
        if wet and self._current_delay() < self._days:
            await self.coordinator.async_set_rain_delay(self._days)
            self._last_skipped = self._last_check
            _LOGGER.info(
                "Rain skip: %s%% chance of rain, rain delay set to %s day(s)",
                chance,
                self._days,
            )
            self.hass.bus.async_fire(
                EVENT_RAIN_SKIP,
                {
                    "entity_id": self.entity_id,
                    "rain_chance": chance,
                    "condition": condition,
                    "rain_delay_days": self._days,
                },
            )
        self.async_write_ha_state()

    def _current_delay(self) -> int:
        data = self.coordinator.rainbird.data
        return int(getattr(data, "rain_delay", 0) or 0)

    async def _async_todays_forecast(self) -> tuple[int | None, str | None]:
        if self.hass.states.get(self._weather) is None:
            raise HomeAssistantError(f"{self._weather} not found")
        response = await self.hass.services.async_call(
            "weather",
            "get_forecasts",
            {"type": "daily"},
            target={"entity_id": self._weather},
            blocking=True,
            return_response=True,
        )
        forecasts = (response or {}).get(self._weather, {}).get("forecast") or []
        if not forecasts:
            raise HomeAssistantError(f"{self._weather} has no daily forecast")
        today = dt_util.now().date()
        entry = next(
            (
                f
                for f in forecasts
                if (when := dt_util.parse_datetime(str(f.get("datetime"))))
                and dt_util.as_local(when).date() == today
            ),
            forecasts[0],
        )
        chance = entry.get("precipitation_probability")
        return (
            int(chance) if chance is not None else None,
            entry.get("condition"),
        )
