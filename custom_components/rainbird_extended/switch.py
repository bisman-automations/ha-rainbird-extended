"""Weather switches: rain skip, freeze skip and weather adjustment.

Each one, while on, checks the weather once a day at the check time from the
options. Freeze skip also watches an optional temperature sensor.
"""

from __future__ import annotations

from datetime import datetime, time
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from . import RainbirdExtendedConfigEntry
from .const import (
    CONF_ADJUST_HIGH_PERCENT,
    CONF_ADJUST_HIGH_TEMPERATURE,
    CONF_ADJUST_LOW_PERCENT,
    CONF_ADJUST_LOW_TEMPERATURE,
    CONF_FREEZE_DELAY_DAYS,
    CONF_FREEZE_SENSOR,
    CONF_FREEZE_TEMPERATURE,
    CONF_RAIN_CHANCE,
    CONF_RAIN_CHECK_TIME,
    CONF_RAIN_DELAY_DAYS,
    CONF_TEMPERATURE_UNIT,
    CONF_WEATHER_ENTITY,
    DEFAULT_ADJUST_HIGH_PERCENT,
    DEFAULT_ADJUST_LOW_PERCENT,
    DEFAULT_FREEZE_DELAY_DAYS,
    DEFAULT_RAIN_CHANCE,
    DEFAULT_RAIN_CHECK_TIME,
    DEFAULT_RAIN_DELAY_DAYS,
    EVENT_FREEZE_SKIP,
    EVENT_RAIN_SKIP,
    EVENT_WEATHER_ADJUSTMENT,
    RAINY_CONDITIONS,
    SEASONAL_ADJUST_MIN,
    TEMPERATURE_DEFAULTS,
)
from .coordinator import RainbirdExtendedCoordinator
from .entity import RainbirdExtendedControllerEntity
from .weather import (
    async_todays_forecast,
    default_unit,
    forecast_temperature,
    sensor_temperature,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RainbirdExtendedConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the weather switches the options call for."""
    coordinator = entry.runtime_data
    options = dict(entry.options)
    unit = options.get(CONF_TEMPERATURE_UNIT) or default_unit(hass)
    entities: list[Entity] = []
    if options.get(CONF_WEATHER_ENTITY):
        entities.append(RainbirdRainSkip(coordinator, options))
    if options.get(CONF_WEATHER_ENTITY) or options.get(CONF_FREEZE_SENSOR):
        entities.append(RainbirdFreezeSkip(coordinator, options, unit))
    if (
        options.get(CONF_WEATHER_ENTITY)
        and coordinator.supports_water_budget
        and coordinator.can_set_water_budget
    ):
        entities.append(RainbirdWeatherAdjustment(coordinator, options, unit))
    async_add_entities(entities)


def _option(options: dict[str, Any], key: str, unit: str) -> float:
    """A temperature option, or its default for the unit."""
    value = options.get(key)
    if value is None:
        value = TEMPERATURE_DEFAULTS.get(unit, TEMPERATURE_DEFAULTS["°C"])[key]
    return float(value)


class _DailyWeatherSwitch(
    RainbirdExtendedControllerEntity, SwitchEntity, RestoreEntity
):
    """A switch that, while on, runs a check every day at the check time."""

    _default_on = True

    def __init__(
        self,
        coordinator: RainbirdExtendedCoordinator,
        options: dict[str, Any],
        key: str,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, key)
        self._attr_translation_key = key
        self._weather: str | None = options.get(CONF_WEATHER_ENTITY)
        check = dt_util.parse_time(
            options.get(CONF_RAIN_CHECK_TIME, DEFAULT_RAIN_CHECK_TIME)
        )
        self._check: time = check or time(4, 0)
        self._attr_is_on = self._default_on
        self._last_check: datetime | None = None

    async def async_added_to_hass(self) -> None:
        """Restore on/off and schedule the daily check."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None and last.state in (
            STATE_ON,
            STATE_OFF,
        ):
            self._attr_is_on = last.state == STATE_ON
            self._restore(last.attributes)
        self.async_on_remove(
            async_track_time_change(
                self.hass,
                self._async_scheduled_check,
                hour=self._check.hour,
                minute=self._check.minute,
                second=self._check.second,
            )
        )

    def _restore(self, attributes: dict[str, Any]) -> None:
        """Restore what the last check found."""

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on."""
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off."""
        self._attr_is_on = False
        self.async_write_ha_state()

    @callback
    def _async_scheduled_check(self, _now: datetime) -> None:
        if self.is_on:
            self.coordinator.config_entry.async_create_background_task(
                self.hass, self._async_run_check(), f"{self.entity_id} check"
            )

    async def _async_run_check(self) -> None:
        try:
            await self.async_check()
        except HomeAssistantError as err:
            _LOGGER.warning("%s: %s", self.entity_id, err)
        self.async_write_ha_state()

    async def async_check(self) -> None:
        """Run the daily check."""
        raise NotImplementedError

    def _current_delay(self) -> int:
        data = self.coordinator.rainbird.data
        return int(getattr(data, "rain_delay", 0) or 0)

    @staticmethod
    def _iso(value: datetime | None) -> str | None:
        return value.isoformat() if value else None


class RainbirdRainSkip(_DailyWeatherSwitch):
    """While on, check the forecast each day and set a rain delay if wet."""

    def __init__(
        self, coordinator: RainbirdExtendedCoordinator, options: dict[str, Any]
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, options, "rain_skip")
        self._threshold = int(options.get(CONF_RAIN_CHANCE, DEFAULT_RAIN_CHANCE))
        self._days = int(options.get(CONF_RAIN_DELAY_DAYS, DEFAULT_RAIN_DELAY_DAYS))
        self._last_chance: int | None = None
        self._last_skipped: datetime | None = None

    def _restore(self, attributes: dict[str, Any]) -> None:
        if skipped := attributes.get("last_skipped"):
            self._last_skipped = dt_util.parse_datetime(skipped)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return what the last check found."""
        return {
            "weather_entity": self._weather,
            "rain_chance_threshold": self._threshold,
            "check_time": self._check.isoformat(),
            "last_check": self._iso(self._last_check),
            "last_rain_chance": self._last_chance,
            "last_skipped": self._iso(self._last_skipped),
        }

    async def async_check_forecast(self) -> None:
        """Set a rain delay if today's forecast is wet enough."""
        await self._async_run_check()

    async def async_check(self) -> None:
        """Set a rain delay if today's forecast is wet enough."""
        assert self._weather is not None
        forecast = await async_todays_forecast(self.hass, self._weather)
        chance_value = forecast.get("precipitation_probability")
        chance = int(chance_value) if chance_value is not None else None
        condition = forecast.get("condition")
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


class RainbirdFreezeSkip(_DailyWeatherSwitch):
    """While on, set a rain delay on cold days and stop watering in a freeze.

    Each day at the check time, today's forecast low (and the temperature
    sensor, if one is chosen) is compared with the freeze temperature. With a
    sensor, watering is also stopped whenever it reads at or below it.
    Blowouts are never stopped: they're air, and done in the cold.
    """

    def __init__(
        self,
        coordinator: RainbirdExtendedCoordinator,
        options: dict[str, Any],
        unit: str,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, options, "freeze_skip")
        self._unit = unit
        self._sensor: str | None = options.get(CONF_FREEZE_SENSOR)
        self._threshold = _option(options, CONF_FREEZE_TEMPERATURE, unit)
        self._days = int(options.get(CONF_FREEZE_DELAY_DAYS, DEFAULT_FREEZE_DELAY_DAYS))
        self._last_low: float | None = None
        self._last_skipped: datetime | None = None
        self._last_stopped: datetime | None = None

    async def async_added_to_hass(self) -> None:
        """Also watch the temperature sensor."""
        await super().async_added_to_hass()
        if self._sensor:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [self._sensor], self._async_sensor_changed
                )
            )
            # Zones starting while it's already freezing.
            self.async_on_remove(
                self.coordinator.async_add_listener(self._async_check_running)
            )

    def _restore(self, attributes: dict[str, Any]) -> None:
        if skipped := attributes.get("last_skipped"):
            self._last_skipped = dt_util.parse_datetime(skipped)
        if stopped := attributes.get("last_stopped"):
            self._last_stopped = dt_util.parse_datetime(stopped)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the threshold and what the last check found."""
        return {
            "weather_entity": self._weather,
            "temperature_sensor": self._sensor,
            "freeze_temperature": self._threshold,
            "temperature_unit": self._unit,
            "check_time": self._check.isoformat(),
            "last_check": self._iso(self._last_check),
            "last_low": self._last_low,
            "last_skipped": self._iso(self._last_skipped),
            "last_stopped": self._iso(self._last_stopped),
        }

    def _sensor_value(self) -> float | None:
        if not self._sensor:
            return None
        return sensor_temperature(self.hass, self._sensor, self._unit)

    async def async_check(self) -> None:
        """Set a rain delay if today's low (or the sensor) is freezing."""
        temperatures: list[float] = []
        if self._weather:
            forecast = await async_todays_forecast(self.hass, self._weather)
            low = forecast_temperature(
                self.hass, self._weather, forecast, "templow", self._unit
            )
            if low is not None:
                temperatures.append(low)
        if (value := self._sensor_value()) is not None:
            temperatures.append(value)
        self._last_check = dt_util.utcnow()
        self._last_low = round(min(temperatures), 1) if temperatures else None
        if (
            self._last_low is not None
            and self._last_low <= self._threshold
            and self._current_delay() < self._days
        ):
            await self.coordinator.async_set_rain_delay(self._days)
            self._last_skipped = self._last_check
            _LOGGER.info(
                "Freeze skip: low of %s%s, rain delay set to %s day(s)",
                self._last_low,
                self._unit,
                self._days,
            )
            self._fire("forecast", self._last_low)

    @callback
    def _async_sensor_changed(self, event: Event[EventStateChangedData]) -> None:
        self._async_check_running()

    @callback
    def _async_check_running(self) -> None:
        """Stop watering if the sensor reads freezing."""
        if not self.is_on or self.coordinator.blowout_running:
            return
        if not (self.coordinator.active_zones or self.coordinator.sequence_running):
            return
        value = self._sensor_value()
        if value is None or value > self._threshold:
            return
        _LOGGER.info(
            "Freeze skip: %s is %s%s, stopping irrigation",
            self._sensor,
            value,
            self._unit,
        )
        self._last_stopped = dt_util.utcnow()
        self._fire("sensor", value)
        self.coordinator.config_entry.async_create_background_task(
            self.hass, self._async_stop(), "freeze skip stop"
        )

    async def _async_stop(self) -> None:
        try:
            await self.coordinator.async_stop()
        except HomeAssistantError as err:
            _LOGGER.warning("Freeze skip: could not stop irrigation: %s", err)
        self.async_write_ha_state()

    def _fire(self, reason: str, temperature: float) -> None:
        self.hass.bus.async_fire(
            EVENT_FREEZE_SKIP,
            {
                "entity_id": self.entity_id,
                "reason": reason,
                "temperature": temperature,
                "temperature_unit": self._unit,
                "rain_delay_days": self._days if reason == "forecast" else None,
            },
        )


class RainbirdWeatherAdjustment(_DailyWeatherSwitch):
    """While on, set the seasonal adjustment from today's forecast high.

    The percentage goes in a straight line from the low percent at the low
    temperature to the high percent at the high temperature, and stays at
    those ends beyond them. It's rounded to 5% and applied to every program.
    """

    _default_on = False

    def __init__(
        self,
        coordinator: RainbirdExtendedCoordinator,
        options: dict[str, Any],
        unit: str,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, options, "weather_adjustment")
        self._unit = unit
        self._low_temp = _option(options, CONF_ADJUST_LOW_TEMPERATURE, unit)
        self._high_temp = _option(options, CONF_ADJUST_HIGH_TEMPERATURE, unit)
        self._low_percent = int(
            options.get(CONF_ADJUST_LOW_PERCENT, DEFAULT_ADJUST_LOW_PERCENT)
        )
        self._high_percent = int(
            options.get(CONF_ADJUST_HIGH_PERCENT, DEFAULT_ADJUST_HIGH_PERCENT)
        )
        self._last_high: float | None = None
        self._last_percent: int | None = None

    def _restore(self, attributes: dict[str, Any]) -> None:
        self._last_percent = attributes.get("last_percent")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the settings and what the last check set."""
        return {
            "weather_entity": self._weather,
            "low": f"{self._low_percent}% at {self._low_temp:g}{self._unit}",
            "high": f"{self._high_percent}% at {self._high_temp:g}{self._unit}",
            "check_time": self._check.isoformat(),
            "last_check": self._iso(self._last_check),
            "last_high": self._last_high,
            "last_percent": self._last_percent,
        }

    def percent_for(self, high: float) -> int:
        """The seasonal adjustment for a forecast high."""
        span = self._high_temp - self._low_temp
        if span <= 0:
            fraction = 1.0 if high >= self._high_temp else 0.0
        else:
            fraction = min(max((high - self._low_temp) / span, 0.0), 1.0)
        percent = self._low_percent + fraction * (
            self._high_percent - self._low_percent
        )
        percent = 5 * round(percent / 5)
        return int(
            min(max(percent, SEASONAL_ADJUST_MIN), self.coordinator.max_seasonal_adjust)
        )

    async def async_check(self) -> None:
        """Set every program's seasonal adjustment from today's high."""
        assert self._weather is not None
        forecast = await async_todays_forecast(self.hass, self._weather)
        high = forecast_temperature(
            self.hass, self._weather, forecast, "temperature", self._unit
        )
        self._last_check = dt_util.utcnow()
        if high is None:
            raise HomeAssistantError(f"{self._weather} has no forecast high")
        self._last_high = round(high, 1)
        percent = self.percent_for(high)
        changed = [
            key
            for key in self.coordinator.water_budget_keys
            if self.coordinator.water_budgets.get(key) != percent
        ]
        for key in changed:
            await self.coordinator.async_set_water_budget(key, percent)
        self._last_percent = percent
        _LOGGER.info(
            "Weather adjustment: high of %s%s, seasonal adjustment %s%%",
            self._last_high,
            self._unit,
            percent,
        )
        self.hass.bus.async_fire(
            EVENT_WEATHER_ADJUSTMENT,
            {
                "entity_id": self.entity_id,
                "high": self._last_high,
                "temperature_unit": self._unit,
                "percent": percent,
                "changed": bool(changed),
            },
        )
