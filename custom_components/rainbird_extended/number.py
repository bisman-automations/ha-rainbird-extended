"""Valve runtime and flow rate numbers for each Rain Bird zone."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberMode, RestoreNumber
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import RainbirdExtendedConfigEntry
from .const import (
    FLOW_RATE_MAX,
    FLOW_RATE_STEP,
    RUNTIME_MAX_SECONDS,
    RUNTIME_MIN_SECONDS,
    RUNTIME_STEP_SECONDS,
)
from .coordinator import RainbirdExtendedCoordinator
from .entity import RainbirdExtendedZoneEntity, water_units


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RainbirdExtendedConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a valve runtime and flow rate for every zone."""
    coordinator = entry.runtime_data
    flow_unit, _ = water_units(hass)
    async_add_entities(
        entity
        for zone in coordinator.linked_zones
        for entity in (
            RainbirdZoneRuntime(coordinator, zone),
            RainbirdZoneFlowRate(coordinator, zone, flow_unit),
        )
    )


class RainbirdZoneRuntime(RainbirdExtendedZoneEntity, RestoreNumber):
    """How long the zone runs when its valve is opened.

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


class RainbirdZoneFlowRate(RainbirdExtendedZoneEntity, RestoreNumber):
    """How much water the zone uses per minute; 0 means don't track water."""

    _attr_device_class = NumberDeviceClass.VOLUME_FLOW_RATE
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = FLOW_RATE_MAX
    _attr_native_step = FLOW_RATE_STEP
    _attr_translation_key = "flow_rate"

    def __init__(
        self, coordinator: RainbirdExtendedCoordinator, zone: int, unit: str
    ) -> None:
        """Initialize the flow rate number."""
        super().__init__(coordinator, zone, "flow_rate")
        self._attr_native_unit_of_measurement = unit
        self._attr_native_value = 0.0

    async def async_added_to_hass(self) -> None:
        """Restore the flow rate and share it with the water sensor."""
        await super().async_added_to_hass()
        if (
            last := await self.async_get_last_number_data()
        ) is not None and last.native_value is not None:
            self._attr_native_value = float(last.native_value)
        self.coordinator.flow_rates[self._zone] = float(self._attr_native_value)

    async def async_set_native_value(self, value: float) -> None:
        """Set a new flow rate."""
        self._attr_native_value = round(float(value), 1)
        self.coordinator.flow_rates[self._zone] = self._attr_native_value
        self.async_write_ha_state()
