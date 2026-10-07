"""Run started / finished events for each zone."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import RainbirdExtendedConfigEntry
from .coordinator import (
    EVENT_FINISHED,
    EVENT_STARTED,
    RainbirdExtendedCoordinator,
    ZoneRun,
)
from .entity import RainbirdExtendedZoneEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RainbirdExtendedConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a run event entity for every zone."""
    coordinator = entry.runtime_data
    async_add_entities(
        RainbirdZoneRunEvent(coordinator, zone) for zone in coordinator.linked_zones
    )


class RainbirdZoneRunEvent(RainbirdExtendedZoneEntity, EventEntity):
    """Fires when the zone starts and finishes running, however it was started."""

    _attr_translation_key = "run"

    def __init__(self, coordinator: RainbirdExtendedCoordinator, zone: int) -> None:
        """Initialize the event entity."""
        super().__init__(coordinator, zone, "run")
        self._attr_event_types = [EVENT_STARTED, EVENT_FINISHED]

    async def async_added_to_hass(self) -> None:
        """Listen for runs of this zone."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_run_listener(self._zone, self._handle_run)
        )

    @callback
    def _handle_run(self, event: str, run: ZoneRun) -> None:
        attributes: dict[str, str | int | None] = {
            "source": run.source,
            "start": run.start.isoformat(),
        }
        if event == EVENT_FINISHED:
            duration = run.duration
            attributes["end"] = run.end.isoformat() if run.end else None
            attributes["duration"] = (
                round(duration.total_seconds()) if duration else None
            )
        self._trigger_event(event, attributes)
        self.async_write_ha_state()
