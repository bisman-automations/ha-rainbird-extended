"""Controller buttons: run a program, run all zones, stop."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import RainbirdExtendedConfigEntry
from .coordinator import RainbirdExtendedCoordinator
from .entity import RainbirdExtendedControllerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RainbirdExtendedConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the controller buttons."""
    coordinator = entry.runtime_data
    entities: list[Entity] = [
        RainbirdRunAllZones(coordinator),
        RainbirdStopIrrigation(coordinator),
    ]
    entities.extend(
        RainbirdRunProgram(coordinator, program)
        for program in range(coordinator.max_programs)
    )
    async_add_entities(entities)


class RainbirdRunProgram(RainbirdExtendedControllerEntity, ButtonEntity):
    """Start one of the controller's programs."""

    _attr_translation_key = "run_program"

    def __init__(self, coordinator: RainbirdExtendedCoordinator, program: int) -> None:
        """Initialize the button."""
        letter = chr(ord("A") + program)
        super().__init__(coordinator, f"run_program_{letter.lower()}")
        self._program = program
        self._attr_translation_placeholders = {"program": letter}

    async def async_press(self) -> None:
        """Start the program."""
        await self.coordinator.async_run_program(self._program)


class RainbirdRunAllZones(RainbirdExtendedControllerEntity, ButtonEntity):
    """Run every zone once, in order, each for its valve runtime."""

    _attr_translation_key = "run_all_zones"

    def __init__(self, coordinator: RainbirdExtendedCoordinator) -> None:
        """Initialize the button."""
        super().__init__(coordinator, "run_all_zones")

    async def async_press(self) -> None:
        """Start the sequence."""
        await self.coordinator.async_run_all_zones()


class RainbirdStopIrrigation(RainbirdExtendedControllerEntity, ButtonEntity):
    """Stop all irrigation, including "Run all zones"."""

    _attr_translation_key = "stop_irrigation"

    def __init__(self, coordinator: RainbirdExtendedCoordinator) -> None:
        """Initialize the button."""
        super().__init__(coordinator, "stop_irrigation")

    async def async_press(self) -> None:
        """Stop irrigation."""
        await self.coordinator.async_stop()
