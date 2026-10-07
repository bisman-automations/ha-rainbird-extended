"""Irrigating binary sensor for the controller."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import RainbirdExtendedConfigEntry
from .coordinator import RainbirdExtendedCoordinator
from .entity import RainbirdExtendedControllerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RainbirdExtendedConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the irrigating sensor."""
    async_add_entities([RainbirdIrrigating(entry.runtime_data)])


class RainbirdIrrigating(RainbirdExtendedControllerEntity, BinarySensorEntity):
    """On while any zone is running, however it was started."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_translation_key = "irrigating"

    def __init__(self, coordinator: RainbirdExtendedCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "irrigating")

    @property
    def _running(self) -> list[int]:
        return sorted(set(self.coordinator.data or {}) | self.coordinator.active_zones)

    @property
    def is_on(self) -> bool:
        """Return whether a zone is running."""
        return bool(self._running)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the running zones and when the current run ends."""
        end_times = self.coordinator.data or {}
        ends = [end for zone in self._running if (end := end_times.get(zone))]
        return {
            "zones": self._running,
            "end": max(ends).isoformat() if ends else None,
            "run_all_zones": self.coordinator.sequence_running
            and not self.coordinator.blowout_running,
            "blowout": self.coordinator.blowout_running,
        }
