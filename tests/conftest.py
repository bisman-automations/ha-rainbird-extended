"""Fixtures for Rain Bird Extended tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from pyrainbird.data import ControllerState, ModelAndVersion
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rainbird_extended.const import CONF_RAINBIRD_ENTRY_ID, DOMAIN
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

MAC_ADDRESS = "4C:A1:61:00:11:22"
RAINBIRD_UNIQUE_ID = "4c:a1:61:00:11:22"
ZONES = {1, 2, 3}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow loading custom_components in every test."""


def controller_state(remaining: int = 0, station: int = 0) -> ControllerState:
    """Build a combined controller state response."""
    return ControllerState(
        delay_setting=0,
        sensor_state=0,
        irrigation_state=1,
        seasonal_adjust=0,
        remaining_runtime=remaining,
        active_station=station,
        device_time=datetime.datetime(2026, 10, 6, 12, 0, 0),
    )


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Skip setting the integration up (config flow tests)."""
    with patch(
        "custom_components.rainbird_extended.async_setup_entry", return_value=True
    ) as mock:
        yield mock


@pytest.fixture
def active_zones() -> set[int]:
    """Zones the fake controller reports as running."""
    return set()


@pytest.fixture
def controller(active_zones: set[int]) -> MagicMock:
    """A fake Rain Bird controller."""
    controller = MagicMock()
    controller.get_model_and_version = AsyncMock(return_value=ModelAndVersion(5, 9, 12))
    controller.get_available_stations = AsyncMock(
        return_value=SimpleNamespace(active_set=set(ZONES))
    )
    controller.get_zone_states = AsyncMock(
        side_effect=lambda: SimpleNamespace(active_set=set(active_zones))
    )
    controller.get_rain_sensor_state = AsyncMock(return_value=False)
    controller.get_rain_delay = AsyncMock(return_value=0)
    controller.get_combined_controller_state = AsyncMock(
        return_value=controller_state()
    )
    # Behave like the controller: starting a zone makes it the only active one.
    controller.irrigate_zone = AsyncMock(
        side_effect=lambda zone, minutes: (active_zones.clear(), active_zones.add(zone))
    )
    controller.stop_irrigation = AsyncMock(side_effect=active_zones.clear)
    return controller


@pytest.fixture
def rainbird_entry(hass: HomeAssistant) -> MockConfigEntry:
    """The core Rain Bird config entry."""
    entry = MockConfigEntry(
        domain="rainbird",
        title="Rain Bird",
        unique_id=RAINBIRD_UNIQUE_ID,
        data={
            "host": "example.com",
            "password": "password",
            "serial_number": 0x12635436566,
            "mac": MAC_ADDRESS,
        },
        options={"duration": 6},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def extended_entry(
    hass: HomeAssistant, rainbird_entry: MockConfigEntry
) -> MockConfigEntry:
    """The Rain Bird Extended config entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Rain Bird Extended",
        unique_id=rainbird_entry.entry_id,
        data={CONF_RAINBIRD_ENTRY_ID: rainbird_entry.entry_id},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def mock_rainbird(controller: MagicMock) -> Generator[MagicMock]:
    """Make the core Rain Bird integration talk to the fake controller."""
    with (
        patch(
            "homeassistant.components.rainbird.create_controller",
            AsyncMock(return_value=controller),
        ),
        patch(
            "homeassistant.components.rainbird.async_create_clientsession",
            return_value=MagicMock(close=AsyncMock()),
        ),
        patch("homeassistant.components.rainbird.PLATFORMS", [Platform.SWITCH]),
    ):
        yield controller


@pytest.fixture
async def setup_integrations(
    hass: HomeAssistant,
    mock_rainbird: MagicMock,
    rainbird_entry: MockConfigEntry,
    extended_entry: MockConfigEntry,
) -> AsyncGenerator[MagicMock]:
    """Set up core Rain Bird and Rain Bird Extended."""
    assert await hass.config_entries.async_setup(rainbird_entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(extended_entry.entry_id)
    await hass.async_block_till_done()
    yield mock_rainbird
    # Unload while the core platforms are still patched.
    await hass.config_entries.async_unload(extended_entry.entry_id)
    await hass.config_entries.async_unload(rainbird_entry.entry_id)
