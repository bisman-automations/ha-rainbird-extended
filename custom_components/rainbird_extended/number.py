"""Valve runtime number for each Rain Bird zone."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberMode, RestoreNumber
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import RainbirdExtendedConfigEntry
from .const import RUNTIME_MAX_SECONDS, RUNTIME_MIN_SECONDS, RUNTIME_STEP_SECONDS
from .coordinator import RainbirdExtendedCoordinator
from .entity import RainbirdExtendedZoneEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RainbirdExtendedConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a valve runtime for every zone."""
    coordinator = entry.runtime_data
    async_add_entities(
        RainbirdZoneRuntime(coordinator, zone) for zone in coordinator.linkable_zones()
    )


class RainbirdZoneRuntime(RainbirdExtendedZoneEntity, RestoreNumber):
    """How long the zone runs when its valve or switch is turned on.

    Stored in seconds because HomeKit's "Set Duration" uses seconds; the
    controller only accepts whole minutes, so the step is 60.
    """

    _attr_device_class = NumberDeviceClass.DURATION
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = RUNTIME_MIN_SECONDS
    _attr_native_max_value = RUNTIME_MAX_SECONDS
    _attr_native_step = RUNTIME_STEP_SECONDS
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_translation_key = "valve_runtime"

    def __init__(self, coordinator: RainbirdExtendedCoordinator, zone: int) -> None:
        """Initialize the runtime number."""
        super().__init__(coordinator, zone, "valve_runtime")
        self._attr_native_value = coordinator.default_runtime

    async def async_added_to_hass(self) -> None:
        """Restore the last runtime and share it with the valve."""
        await super().async_added_to_hass()
        if (
            last := await self.async_get_last_number_data()
        ) is not None and last.native_value is not None:
            self._attr_native_value = self._clamp(last.native_value)
        self.coordinator.runtimes[self._zone] = int(self._attr_native_value)

    async def async_set_native_value(self, value: float) -> None:
        """Set a new runtime, rounded to whole minutes."""
        self._attr_native_value = self._clamp(value)
        self.coordinator.runtimes[self._zone] = int(self._attr_native_value)
        self.async_write_ha_state()

    @staticmethod
    def _clamp(value: float) -> int:
        minutes = max(1, round(value / RUNTIME_STEP_SECONDS))
        return min(
            max(minutes * RUNTIME_STEP_SECONDS, RUNTIME_MIN_SECONDS),
            RUNTIME_MAX_SECONDS,
        )
