"""Tests for the Rain Bird Extended sidebar panel data."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rainbird_extended.const import (
    CONF_STOP_ACTION,
    CONF_WEATHER_ENTITY,
    DEFAULT_STOP_ACTION,
)
from custom_components.rainbird_extended.coordinator import RainbirdExtendedCoordinator
from custom_components.rainbird_extended.panel import (
    _async_add_forecasts,
    _panel_config,
    _program_summaries,
)
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .conftest import RAINBIRD_UNIQUE_ID


def test_panel_config_lists_controllers_without_credentials(
    hass: HomeAssistant,
    extended_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Expose controller status, not saved config-entry credentials/options."""
    stop_button = entity_registry.async_get_or_create(
        "button",
        "rainbird_extended",
        f"{RAINBIRD_UNIQUE_ID}-stop_irrigation",
        config_entry=extended_entry,
        suggested_object_id="rain_bird_controller_stop_irrigation",
    )
    result = _panel_config(hass)

    assert len(result["controllers"]) == 1
    controller = result["controllers"][0]
    assert controller["entry_id"] == extended_entry.entry_id
    assert controller["title"] == extended_entry.title
    assert controller["zones"] == []
    assert controller["stop_button"] == stop_button.entity_id
    assert controller["stop_action"] == DEFAULT_STOP_ACTION
    assert "options" not in controller
    assert "data" not in controller
    assert "password" not in str(result)


def test_panel_config_reports_configured_stop_action(
    hass: HomeAssistant,
    extended_entry: MockConfigEntry,
) -> None:
    """Return the selected action without exposing all config entry options."""
    hass.config_entries.async_update_entry(
        extended_entry, options={CONF_STOP_ACTION: "stop_irrigation"}
    )

    result = _panel_config(hass)

    assert result["controllers"][0]["stop_action"] == "stop_irrigation"
    assert "options" not in result["controllers"][0]


def test_panel_config_exposes_entities_for_live_controller_status(
    hass: HomeAssistant,
    rainbird_entry: MockConfigEntry,
    extended_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Expose the Rain Bird status, zone switches and irrigation sensor entities."""
    rain_delay = entity_registry.async_get_or_create(
        "sensor",
        "rainbird",
        f"{RAINBIRD_UNIQUE_ID}-raindelay",
        config_entry=rainbird_entry,
        suggested_object_id="rain_bird_controller_raindelay",
    )
    zone_switch = entity_registry.async_get_or_create(
        "switch",
        "rainbird",
        f"{RAINBIRD_UNIQUE_ID}-1",
        config_entry=rainbird_entry,
        suggested_object_id="rain_bird_sprinkler_1",
    )
    irrigating = entity_registry.async_get_or_create(
        "binary_sensor",
        "rainbird_extended",
        f"{RAINBIRD_UNIQUE_ID}-irrigating",
        config_entry=extended_entry,
        suggested_object_id="rain_bird_controller_irrigating",
    )

    controller = _panel_config(hass)["controllers"][0]

    assert controller["rainbird_status_entities"] == sorted(
        [rain_delay.entity_id, zone_switch.entity_id]
    )
    assert controller["irrigating_entity"] == irrigating.entity_id
    assert controller["watering_entities"] == [zone_switch.entity_id]
    assert controller["rainbird_loaded"] is False


def test_panel_config_includes_zone_numbers(
    hass: HomeAssistant,
    rainbird_entry: MockConfigEntry,
    extended_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Return zone name and editable number details for each Rain Bird device."""
    zone_device = device_registry.async_get_or_create(
        config_entry_id=rainbird_entry.entry_id,
        identifiers={("rainbird", f"{RAINBIRD_UNIQUE_ID}-1")},
        name="Rain Bird Sprinkler 1",
    )
    device_registry.async_update_device(zone_device.id, name_by_user="Front Boulevard")
    switch = entity_registry.async_get_or_create(
        "switch",
        "rainbird",
        f"{RAINBIRD_UNIQUE_ID}-1",
        config_entry=rainbird_entry,
        device_id=zone_device.id,
        suggested_object_id="rain_bird_sprinkler_1",
    )
    hass.states.async_set(switch.entity_id, "off", {"friendly_name": "Front Boulevard"})
    entity = entity_registry.async_get_or_create(
        "number",
        "rainbird_extended",
        f"{RAINBIRD_UNIQUE_ID}-1-valve_runtime",
        config_entry=extended_entry,
        device_id=zone_device.id,
        suggested_object_id="rain_bird_sprinkler_1_valve_runtime",
    )
    hass.states.async_set(
        entity.entity_id,
        "600",
        {
            "friendly_name": "Front Boulevard Valve runtime",
            "min": 60,
            "max": 86400,
            "step": 60,
            "unit_of_measurement": "s",
        },
    )

    result = _panel_config(hass)

    assert result["controllers"][0]["zones"] == [
        {
            "zone": 1,
            "name": "Front Boulevard",
            "numbers": [
                {
                    "entity_id": entity.entity_id,
                    "name": "Front Boulevard Valve runtime",
                    "value": "600",
                    "min": 60,
                    "max": 86400,
                    "step": 60,
                    "unit": "s",
                }
            ],
        }
    ]


def test_panel_config_includes_weather_and_controller_rain_delay(
    hass: HomeAssistant,
    rainbird_entry: MockConfigEntry,
    extended_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Expose configured weather and current Rain Bird controller delay."""
    rain_delay = entity_registry.async_get_or_create(
        "number",
        "rainbird",
        f"{RAINBIRD_UNIQUE_ID}-rain-delay",
        config_entry=rainbird_entry,
        suggested_object_id="rain_bird_controller_rain_delay",
    )
    hass.states.async_set(
        rain_delay.entity_id,
        "3",
        {
            "min": 0,
            "max": 14,
            "step": 1,
            "unit_of_measurement": "d",
        },
    )
    hass.config_entries.async_update_entry(
        extended_entry, options={CONF_WEATHER_ENTITY: "weather.backyard"}
    )

    controller = _panel_config(hass)["controllers"][0]

    assert controller["weather_entity"] == "weather.backyard"
    assert controller["rain_delay"] == {
        "entity_id": rain_delay.entity_id,
        "name": "Rain delay",
        "value": "3",
        "min": 0,
        "max": 14,
        "step": 1,
        "unit": "d",
    }


async def test_panel_adds_up_to_ten_daily_forecasts(hass: HomeAssistant) -> None:
    """Curate a provider's available forecast days for the dashboard."""
    weather_entity = "weather.backyard"
    hass.states.async_set(weather_entity, "cloudy", {"temperature_unit": "°C"})
    forecasts = [
        {
            "datetime": (
                datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=day)
            ).isoformat(),
            "condition": "partlycloudy",
            "precipitation_probability": day,
            "temperature": 20 + day,
            "templow": 10 + day,
            "extra": "not included",
        }
        for day in range(12)
    ]

    async def get_forecasts(_call: ServiceCall) -> dict[str, Any]:
        return {weather_entity: {"forecast": forecasts}}

    hass.services.async_register(
        "weather",
        "get_forecasts",
        get_forecasts,
        supports_response=SupportsResponse.ONLY,
    )
    controllers = [{"weather_entity": weather_entity}]

    await _async_add_forecasts(hass, controllers)

    assert len(controllers[0]["forecast"]) == 10
    assert controllers[0]["forecast"][0]["condition"] == "partlycloudy"
    assert "extra" not in controllers[0]["forecast"][0]
    assert controllers[0]["forecast_unit"] == "°C"
    assert controllers[0]["forecast_error"] is None


def test_program_summaries_show_configuration_and_next_run() -> None:
    """Configured programs show their next run while empty programs are off."""
    now = datetime(2026, 10, 11, 6, tzinfo=UTC)
    programs = _program_summaries(
        {
            "program_a": {
                "frequency": "custom",
                "days": ["mon"],
                "start_time_1": "05:00:00",
                "zones": [1],
            },
            "program_b": {
                "frequency": "cyclic",
                "every_days": 2,
                "start_date": "2026-10-20",
                "start_time_1": "05:00:00",
                "zones": [2],
            },
        },
        3,
        SimpleNamespace(rain_delay_days=0),
        now,
    )

    assert programs == [
        {
            "program": 0,
            "letter": "A",
            "name": "",
            "configured": True,
            "next_run": "2026-10-12",
        },
        {
            "program": 1,
            "letter": "B",
            "name": "",
            "configured": True,
            "next_run": "2026-10-20",
        },
        {
            "program": 2,
            "letter": "C",
            "name": "",
            "configured": False,
            "next_run": None,
        },
    ]


async def test_set_rain_delay_can_wait_for_controller_refresh() -> None:
    """The dashboard waits for Rain Bird to report its updated delay."""
    controller = MagicMock()
    refresh = AsyncMock()
    coordinator = SimpleNamespace(
        controller=controller,
        _async_command=AsyncMock(),
        rainbird=SimpleNamespace(async_request_refresh=refresh),
    )

    await RainbirdExtendedCoordinator.async_set_rain_delay(
        coordinator, 4, wait_for_refresh=True
    )

    coordinator._async_command.assert_awaited_once_with(controller.set_rain_delay, 4)
    refresh.assert_awaited_once()
