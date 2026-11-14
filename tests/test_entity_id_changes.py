"""Entities keep running when their entity ID is changed (Home Assistant 2026.11+)."""

from __future__ import annotations

import importlib
import pkgutil

import pytest

from custom_components import rainbird_extended
from homeassistant.components.number import (
    ATTR_VALUE,
    DOMAIN as NUMBER_DOMAIN,
    SERVICE_SET_VALUE,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity

ADD_REMOVE_METHODS = (
    "async_prepare_to_add_to_hass",
    "async_added_to_hass",
    "async_will_remove_from_hass",
)
RUNTIME = "number.rain_bird_sprinkler_2_valve_runtime"
RENAMED = "number.front_lawn_runtime"


def _entity_classes() -> list[type[Entity]]:
    """Every entity class defined in the integration."""
    classes: list[type[Entity]] = []
    for info in pkgutil.walk_packages(
        rainbird_extended.__path__, f"{rainbird_extended.__name__}."
    ):
        module = importlib.import_module(info.name)
        classes.extend(
            value
            for value in vars(module).values()
            if isinstance(value, type)
            and issubclass(value, Entity)
            and value.__module__ == module.__name__
        )
    return classes


def test_entities_handle_entity_id_changes() -> None:
    """Every class with its own add or remove code also has the rename hook.

    Home Assistant 2026.11 removes and re-adds an entity whose entity ID
    changes unless the class that defines that code (or a subclass of it)
    defines async_entity_id_changed. A base class's hook doesn't cover it.
    """
    classes = _entity_classes()
    assert classes
    missing = sorted(
        cls.__qualname__
        for cls in classes
        if any(method in cls.__dict__ for method in ADD_REMOVE_METHODS)
        and "async_entity_id_changed" not in cls.__dict__
    )
    assert not missing, f"Missing async_entity_id_changed: {', '.join(missing)}"
    # The hook runs on every supported Home Assistant, with or without the
    # base class's version of it.
    for cls in classes:
        if "async_entity_id_changed" in cls.__dict__:
            cls.async_entity_id_changed(object.__new__(cls), "sensor.old")


@pytest.mark.skipif(
    not hasattr(Entity, "async_entity_id_changed"),
    reason="Entities are changed in place from Home Assistant 2026.11",
)
@pytest.mark.usefixtures("setup_integrations")
async def test_renamed_entity_keeps_working(hass: HomeAssistant) -> None:
    """A renamed runtime number moves to its new ID and still takes new values."""
    assert hass.states.get(RUNTIME) is not None
    er.async_get(hass).async_update_entity(RUNTIME, new_entity_id=RENAMED)
    await hass.async_block_till_done()

    assert hass.states.get(RUNTIME) is None
    assert hass.states.get(RENAMED).state == "360"

    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: RENAMED, ATTR_VALUE: 600},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert hass.states.get(RENAMED).state == "600"
    assert hass.states.get(RUNTIME) is None
    entry = hass.config_entries.async_entries("rainbird_extended")[0]
    assert entry.runtime_data.runtimes[2] == 600
