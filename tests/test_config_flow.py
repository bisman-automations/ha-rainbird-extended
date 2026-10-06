"""Tests for the Rain Bird Extended config flow."""

# mock_rainbird keeps the core Rain Bird dependency from touching the network.

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rainbird_extended.const import CONF_RAINBIRD_ENTRY_ID, DOMAIN
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType


async def test_no_rainbird(hass: HomeAssistant) -> None:
    """Abort when there is no Rain Bird controller to extend."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_rainbird"


@pytest.mark.usefixtures("mock_setup_entry", "mock_rainbird")
async def test_single_controller(
    hass: HomeAssistant, rainbird_entry: MockConfigEntry
) -> None:
    """With one controller, create the entry straight away, and only once."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Rain Bird Extended"
    assert result["data"] == {CONF_RAINBIRD_ENTRY_ID: rainbird_entry.entry_id}

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.usefixtures("mock_setup_entry", "mock_rainbird")
async def test_pick_controller(
    hass: HomeAssistant, rainbird_entry: MockConfigEntry
) -> None:
    """With several controllers, ask which one."""
    other = MockConfigEntry(domain="rainbird", title="Back Yard", unique_id="aa")
    other.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_RAINBIRD_ENTRY_ID: other.entry_id}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Back Yard Extended"
