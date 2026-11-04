"""Tests for Rain Bird Extended diagnostics."""

from __future__ import annotations

from unittest.mock import MagicMock

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from homeassistant.components.valve import DOMAIN as VALVE_DOMAIN, SERVICE_OPEN_VALVE
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from .conftest import RAINBIRD_UNIQUE_ID


async def _diagnostics(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, entry: MockConfigEntry
) -> dict:
    assert await async_setup_component(hass, "diagnostics", {})
    return await get_diagnostics_for_config_entry(hass, hass_client, entry)


async def test_diagnostics_idle(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    setup_integrations: MagicMock,
    extended_entry: MockConfigEntry,
) -> None:
    """Idle controller: zones, runtimes and model, with the MAC redacted."""
    diag = await _diagnostics(hass, hass_client, extended_entry)

    assert diag["rainbird_entry"] == {
        "title": "Rain Bird",
        "state": "loaded",
        "unique_id": "**REDACTED**",
        "default_duration_minutes": 6,
    }
    assert diag["core"]["reachable"] is True
    assert diag["core"]["rain_delay_days"] == 0
    assert diag["core"]["rain_sensor"] is False
    assert set(diag["core"]["zone_switches"]) == {"1", "2", "3"}
    # Empty on Home Assistant 2026.9, which has no Run program buttons.
    assert set(diag["core"]["program_buttons"]) <= {"0", "1", "2", "3"}
    assert diag["core"]["calendar"] == "calendar.rain_bird_controller"
    assert "controller" not in diag
    assert diag["options"] == {}
    assert diag["flow_rates"] == {"1": 0.0, "2": 0.0, "3": 0.0}
    assert diag["last_runs"] == {}
    assert diag["run_all_zones_active"] is False
    assert diag["zones"] == [1, 2, 3]
    assert diag["linked_zones"] == [1, 2, 3]
    assert diag["active_zones"] == []
    assert diag["runtimes_seconds"] == {"1": 360, "2": 360, "3": 360}
    assert diag["end_times"] == {}
    assert diag["local_runs"] == {}
    assert diag["last_update_success"] is True
    assert RAINBIRD_UNIQUE_ID not in str(diag)


async def test_diagnostics_running(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    setup_integrations: MagicMock,
    extended_entry: MockConfigEntry,
) -> None:
    """A run started from a valve shows up as an active zone and local run."""
    await hass.services.async_call(
        VALVE_DOMAIN,
        SERVICE_OPEN_VALVE,
        {ATTR_ENTITY_ID: "valve.rain_bird_sprinkler_2"},
        blocking=True,
    )
    await hass.async_block_till_done()

    diag = await _diagnostics(hass, hass_client, extended_entry)

    assert diag["active_zones"] == [2]
    assert set(diag["end_times"]) == {"2"}
    assert diag["end_times"]["2"] is not None
    assert set(diag["local_runs"]) == {"2"}
    assert diag["local_runs"]["2"]["end"] == diag["end_times"]["2"]
    assert diag["last_runs"]["2"]["end"] is None
