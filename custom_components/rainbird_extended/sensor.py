"""Time remaining sensor for each Rain Bird zone."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
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
    """Add a time remaining sensor for every zone."""
    coordinator = entry.runtime_data
    async_add_entities(
        RainbirdZoneTimeRemaining(coordinator, zone)
        for zone in coordinator.linkable_zones()
    )


class RainbirdZoneTimeRemaining(RainbirdExtendedZoneEntity, SensorEntity):
    """When the current run of this zone ends.

    A timestamp, so the frontend shows it as a live countdown ("in 4 minutes")
    and HomeKit can derive "Remaining Duration" from it. Unknown while idle.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_translation_key = "time_remaining"

    def __init__(self, coordinator: RainbirdExtendedCoordinator, zone: int) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, zone, "time_remaining")

    @property
    def native_value(self) -> datetime | None:
        """Return the expected end of the current run."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self._zone)
