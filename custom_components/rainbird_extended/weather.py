"""Today's daily forecast and temperatures, in a given unit."""

from __future__ import annotations

from typing import Any

from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import TemperatureConverter

ATTR_TEMPERATURE_UNIT = "temperature_unit"


async def async_todays_forecast(hass: HomeAssistant, weather: str) -> dict[str, Any]:
    """Return today's daily forecast entry from a weather entity."""
    if hass.states.get(weather) is None:
        raise HomeAssistantError(f"{weather} not found")
    response = await hass.services.async_call(
        "weather",
        "get_forecasts",
        {"type": "daily"},
        target={"entity_id": weather},
        blocking=True,
        return_response=True,
    )
    result = (response or {}).get(weather)
    forecasts = result.get("forecast") if isinstance(result, dict) else None
    entries: list[dict[str, Any]] = [
        f
        for f in (forecasts if isinstance(forecasts, list) else [])
        if isinstance(f, dict)
    ]
    if not entries:
        raise HomeAssistantError(f"{weather} has no daily forecast")
    today = dt_util.now().date()
    return next(
        (
            f
            for f in entries
            if (when := dt_util.parse_datetime(str(f.get("datetime"))))
            and dt_util.as_local(when).date() == today
        ),
        entries[0],
    )


def forecast_temperature(
    hass: HomeAssistant,
    weather: str,
    forecast: dict[str, Any],
    key: str,
    unit: str,
) -> float | None:
    """A forecast temperature ("temperature" or "templow") converted to unit."""
    value = forecast.get(key)
    if value is None:
        return None
    state = hass.states.get(weather)
    source = (
        state.attributes.get(ATTR_TEMPERATURE_UNIT) if state else None
    ) or hass.config.units.temperature_unit
    return _convert(float(value), source, unit)


def sensor_temperature(hass: HomeAssistant, entity_id: str, unit: str) -> float | None:
    """A temperature sensor's value converted to unit, if it has one."""
    state = hass.states.get(entity_id)
    if state is None:
        return None
    try:
        value = float(state.state)
    except ValueError:
        return None
    source = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) or unit
    return _convert(value, source, unit)


def _convert(value: float, source: str, unit: str) -> float:
    if source == unit or source not in TemperatureConverter.VALID_UNITS:
        return value
    return TemperatureConverter.convert(value, source, unit)


def default_unit(hass: HomeAssistant) -> str:
    """The unit temperature options are entered in."""
    if hass.config.units.temperature_unit == UnitOfTemperature.FAHRENHEIT:
        return UnitOfTemperature.FAHRENHEIT
    return UnitOfTemperature.CELSIUS
