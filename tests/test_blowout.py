"""Tests for Blowout sprinklers."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rainbird_extended.config_flow import nest
from custom_components.rainbird_extended.const import (
    CONF_BLOWOUT_CYCLES,
    CONF_BLOWOUT_REST_SECONDS,
    CONF_BLOWOUT_ZONES,
    CONF_DISABLE_RAINBIRD_SWITCHES,
    DOMAIN,
)
from custom_components.rainbird_extended.sequence import Step, plan_blowout
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .helpers import _advance, _calls, _press

BLOWOUT = "button.rain_bird_controller_blowout_sprinklers"
STOP = "button.rain_bird_controller_stop_irrigation"
IRRIGATING = "binary_sensor.rain_bird_controller_irrigating"
RUN_EVENT = "event.rain_bird_sprinkler_1_run"
BURST = timedelta(minutes=1)
REST = timedelta(seconds=150)


def test_plan_blowout() -> None:
    """Bursts per zone, a rest after every burst but the last."""
    assert plan_blowout([1, 2], 2, 60, 150) == [
        Step(1, 60),
        Step(None, 150),
        Step(1, 60),
        Step(None, 150),
        Step(2, 60),
        Step(None, 150),
        Step(2, 60),
    ]
    assert plan_blowout([1], 3, 60, 0) == [Step(1, 60)] * 3


async def test_defaults_match_the_automation(
    hass: HomeAssistant,
    setup_integrations: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """10 one-minute bursts per zone, 2:30 rest, every zone in order."""
    controller = setup_integrations
    await _press(hass, BLOWOUT)
    assert _calls(controller) == [(1, 1)]
    irrigating = hass.states.get(IRRIGATING)
    assert irrigating.state == "on"
    assert irrigating.attributes["blowout"] is True
    assert irrigating.attributes["run_all_zones"] is False

    await _advance(hass, freezer, BURST)
    assert _calls(controller) == [(1, 1)]  # resting
    await _advance(hass, freezer, REST)
    assert _calls(controller) == [(1, 1), (1, 1)]

    for _ in range(8):
        await _advance(hass, freezer, BURST)
        await _advance(hass, freezer, REST)
    assert _calls(controller) == [(1, 1)] * 10

    # Rest after zone 1's last burst, then zone 2.
    await _advance(hass, freezer, BURST)
    await _advance(hass, freezer, REST)
    assert _calls(controller)[-1] == (2, 1)

    for _ in range(9 + 10 * 1):
        await _advance(hass, freezer, BURST)
        await _advance(hass, freezer, REST)
    assert _calls(controller) == [(1, 1)] * 10 + [(2, 1)] * 10 + [(3, 1)] * 10
    await _advance(hass, freezer, BURST)
    await _advance(hass, freezer, REST * 4)
    assert controller.irrigate_zone.await_count == 30
    assert hass.states.get(IRRIGATING).attributes["blowout"] is False


@pytest.mark.parametrize(
    "extended_options",
    [
        {
            CONF_DISABLE_RAINBIRD_SWITCHES: False,
            CONF_BLOWOUT_ZONES: [3, 1],
            CONF_BLOWOUT_CYCLES: 2,
            CONF_BLOWOUT_REST_SECONDS: 30,
        }
    ],
)
async def test_options(
    hass: HomeAssistant,
    setup_integrations: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Zones, order, bursts and rest come from the options."""
    await _press(hass, BLOWOUT)
    for _ in range(4):
        await _advance(hass, freezer, BURST)
        await _advance(hass, freezer, timedelta(seconds=30))
    assert _calls(setup_integrations) == [(3, 1), (3, 1), (1, 1), (1, 1)]


async def test_action_overrides(
    hass: HomeAssistant,
    setup_integrations: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The action can pick zones, bursts, burst length and rest."""
    await hass.services.async_call(
        DOMAIN,
        "blowout",
        {
            ATTR_ENTITY_ID: BLOWOUT,
            "zones": [2],
            "cycles": 3,
            "on_time": "00:02:00",
            "rest": "00:00:00",
        },
        blocking=True,
    )
    await _advance(hass, freezer, timedelta(minutes=2))
    await _advance(hass, freezer, timedelta(minutes=2))
    await _advance(hass, freezer, timedelta(minutes=10))
    assert _calls(setup_integrations) == [(2, 2)] * 3

    with pytest.raises(HomeAssistantError, match="Unknown zones"):
        await hass.services.async_call(
            DOMAIN, "blowout", {ATTR_ENTITY_ID: BLOWOUT, "zones": [9]}, blocking=True
        )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "blowout", {ATTR_ENTITY_ID: STOP}, blocking=True
        )


async def test_stop_ends_blowout(
    hass: HomeAssistant,
    setup_integrations: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Stop irrigation during a rest cancels the remaining bursts."""
    await _press(hass, BLOWOUT)
    await _advance(hass, freezer, BURST)
    await _press(hass, STOP)
    await _advance(hass, freezer, timedelta(minutes=30))
    assert setup_integrations.irrigate_zone.await_count == 1
    assert hass.states.get(IRRIGATING).attributes["blowout"] is False


async def test_run_event_source(
    hass: HomeAssistant, setup_integrations: MagicMock
) -> None:
    """Each burst's run event says it came from the blowout."""
    await _press(hass, BLOWOUT)
    state = hass.states.get(RUN_EVENT)
    assert state.attributes["event_type"] == "started"
    assert state.attributes["source"] == "blowout"


async def test_options_flow_zones(
    hass: HomeAssistant,
    setup_integrations: MagicMock,
    extended_entry: MockConfigEntry,
) -> None:
    """Blowout zones are parsed and checked like Run all zones'."""
    result = await hass.config_entries.options.async_init(extended_entry.entry_id)
    base = {
        CONF_DISABLE_RAINBIRD_SWITCHES: False,
        "cycle_minutes": 0,
        "soak_minutes": 30,
        "rain_chance": 60,
        "rain_check_time": "04:00:00",
        "rain_delay_days": 1,
        CONF_BLOWOUT_CYCLES: 10,
        "blowout_on_minutes": 1,
        CONF_BLOWOUT_REST_SECONDS: 150,
    }
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            nest({**base, CONF_BLOWOUT_ZONES: ["valve.not_a_zone"]}),
        )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        nest(
            {
                **base,
                CONF_BLOWOUT_ZONES: [
                    "valve.rain_bird_sprinkler_2",
                    "valve.rain_bird_sprinkler_1",
                ],
            }
        ),
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert extended_entry.options[CONF_BLOWOUT_ZONES] == [2, 1]
    assert extended_entry.options["run_all_zones"] == []
