"""Valve entity for each Rain Bird zone."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.components.valve import (
    ValveDeviceClass,
    ValveEntity,
    ValveEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import RainbirdExtendedConfigEntry
from .coordinator import RainbirdExtendedCoordinator
from .entity import RainbirdExtendedZoneEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RainbirdExtendedConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a valve for every zone."""
    coordinator = entry.runtime_data
    async_add_entities(
        RainbirdZoneValve(coordinator, zone) for zone in coordinator.linked_zones
    )


class RainbirdZoneValve(RainbirdExtendedZoneEntity, ValveEntity):
    """A Rain Bird zone as a water valve.

    Opening runs the zone for its "Valve runtime". Closing stops irrigation;
    Rain Bird controllers can only stop everything, not a single zone.
    """

    _attr_name = None
    _attr_device_class = ValveDeviceClass.WATER
    _attr_reports_position = False
    _attr_supported_features = ValveEntityFeature.OPEN | ValveEntityFeature.CLOSE
    _attr_translation_key = "zone"

    def __init__(self, coordinator: RainbirdExtendedCoordinator, zone: int) -> None:
        """Initialize the valve."""
        super().__init__(coordinator, zone, "valve")

    @property
    def is_closed(self) -> bool:
        """Closed unless the controller reports the zone running."""
        return self._zone not in self.coordinator.active_zones

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the zone number like the core switch does."""
        return {"zone": self._zone}

    async def async_open_valve(self) -> None:
        """Run the zone for its configured runtime."""
        await self.coordinator.async_start_zone(self._zone)

    async def async_start_zone(self, duration: timedelta) -> None:
        """Run the zone once for the given duration, keeping its valve runtime."""
        await self.coordinator.async_start_zone(
            self._zone, int(duration.total_seconds())
        )

    async def async_close_valve(self) -> None:
        """Stop irrigation."""
        await self.coordinator.async_stop()
