"""Tests for the Rain Bird Extended sidebar panel data."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rainbird_extended.panel import _panel_config
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .conftest import RAINBIRD_UNIQUE_ID


def test_panel_config_lists_controllers_without_credentials(
    hass: HomeAssistant, extended_entry: MockConfigEntry
) -> None:
    """Expose controller status, not saved config-entry credentials/options."""
    result = _panel_config(hass)

    assert len(result["controllers"]) == 1
    controller = result["controllers"][0]
    assert controller["entry_id"] == extended_entry.entry_id
    assert controller["title"] == extended_entry.title
    assert controller["zones"] == []
    assert "options" not in controller
    assert "data" not in controller
    assert "password" not in str(result)


def test_panel_config_includes_zone_numbers(
    hass: HomeAssistant,
    extended_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Return editable zone number entities with their current limits and value."""
    entity = entity_registry.async_get_or_create(
        "number",
        "rainbird_extended",
        f"{RAINBIRD_UNIQUE_ID}-1-valve_runtime",
        config_entry=extended_entry,
        suggested_object_id="rain_bird_sprinkler_1_valve_runtime",
    )
    hass.states.async_set(
        entity.entity_id,
        "600",
        {
            "friendly_name": "Sprinkler 1 Valve runtime",
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
            "numbers": [
                {
                    "entity_id": entity.entity_id,
                    "name": "Sprinkler 1 Valve runtime",
                    "value": "600",
                    "min": 60,
                    "max": 86400,
                    "step": 60,
                    "unit": "s",
                }
            ],
        }
    ]
