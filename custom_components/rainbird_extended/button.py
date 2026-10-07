"""Controller buttons: programs, run all zones, blowout, pause/resume, stop."""

from __future__ import annotations

from datetime import timedelta

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
        RainbirdBlowout(coordinator),
        RainbirdPause(coordinator),
        RainbirdResume(coordinator),
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


class RainbirdBlowout(RainbirdExtendedControllerEntity, ButtonEntity):
    """Blow out the zones with compressed air, using the options' settings.

    The rainbird_extended.blowout action targets this button to override them.
    """

    _attr_translation_key = "blowout"

    def __init__(self, coordinator: RainbirdExtendedCoordinator) -> None:
        """Initialize the button."""
        super().__init__(coordinator, "blowout")

    async def async_press(self) -> None:
        """Start the blowout."""
        await self.coordinator.async_blowout()

    async def async_blowout(
        self,
        zones: list[int] | None = None,
        cycles: int | None = None,
        on_time: timedelta | None = None,
        rest: timedelta | None = None,
    ) -> None:
        """Start a blowout, overriding the options where given."""
        await self.coordinator.async_blowout(
            zones,
            cycles,
            None if on_time is None else int(on_time.total_seconds()),
            None if rest is None else int(rest.total_seconds()),
        )


class RainbirdPause(RainbirdExtendedControllerEntity, ButtonEntity):
    """Pause Run all zones, cycle and soak or a blowout where it is."""

    _attr_translation_key = "pause"

    def __init__(self, coordinator: RainbirdExtendedCoordinator) -> None:
        """Initialize the button."""
        super().__init__(coordinator, "pause")

    @property
    def available(self) -> bool:
        """Only while one of those is running."""
        return super().available and self.coordinator.sequence_running

    async def async_press(self) -> None:
        """Pause."""
        await self.coordinator.async_pause()


class RainbirdResume(RainbirdExtendedControllerEntity, ButtonEntity):
    """Carry on with a paused Run all zones, cycle and soak or blowout."""

    _attr_translation_key = "resume"

    def __init__(self, coordinator: RainbirdExtendedCoordinator) -> None:
        """Initialize the button."""
        super().__init__(coordinator, "resume")

    @property
    def available(self) -> bool:
        """Only while something is paused."""
        return super().available and self.coordinator.paused

    async def async_press(self) -> None:
        """Resume."""
        await self.coordinator.async_resume()


class RainbirdStopIrrigation(RainbirdExtendedControllerEntity, ButtonEntity):
    """Stop all irrigation, including Run all zones and paused sequences."""

    _attr_translation_key = "stop_irrigation"

    def __init__(self, coordinator: RainbirdExtendedCoordinator) -> None:
        """Initialize the button."""
        super().__init__(coordinator, "stop_irrigation")

    async def async_press(self) -> None:
        """Stop irrigation."""
        await self.coordinator.async_stop()
