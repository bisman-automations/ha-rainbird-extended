"""Valve runtime and flow rate per zone; seasonal adjustment per program."""

from __future__ import annotations

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
    RestoreNumber,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import RainbirdExtendedConfigEntry
from .const import (
    FLOW_RATE_MAX,
    FLOW_RATE_STEP,
    RUNTIME_MAX_SECONDS,
    RUNTIME_MIN_SECONDS,
    RUNTIME_STEP_SECONDS,
    SEASONAL_ADJUST_MIN,
)
from .coordinator import RainbirdExtendedCoordinator
from .entity import (
    RainbirdExtendedControllerEntity,
    RainbirdExtendedZoneEntity,
    water_budget_naming,
    water_units,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RainbirdExtendedConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a valve runtime and flow rate for every zone."""
    coordinator = entry.runtime_data
    flow_unit, _ = water_units(hass)
    entities: list[Entity] = [
        entity
        for zone in coordinator.linked_zones
        for entity in (
            RainbirdZoneRuntime(coordinator, zone),
            RainbirdZoneFlowRate(coordinator, zone, flow_unit),
        )
    ]
    if coordinator.supports_water_budget and coordinator.can_set_water_budget:
        entities.extend(
            RainbirdSeasonalAdjustment(coordinator, key)
            for key in coordinator.water_budget_keys
        )
    async_add_entities(entities)


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
        self.coordinator.runtimes[self._zone] = int(self._attr_native_value or 0)

    async def async_set_native_value(self, value: float) -> None:
        """Set a new runtime, rounded to whole minutes."""
        self._attr_native_value = self._clamp(value)
        self.coordinator.runtimes[self._zone] = int(self._attr_native_value)
        self.async_write_ha_state()
        if self._zone in self.coordinator.estimated_zones:
            # Its time remaining is estimated from the runtime: update it.
            await self.coordinator.async_refresh()

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
        self.coordinator.flow_rates[self._zone] = float(self._attr_native_value or 0)

    async def async_set_native_value(self, value: float) -> None:
        """Set a new flow rate."""
        self._attr_native_value = round(float(value), 1)
        self.coordinator.flow_rates[self._zone] = self._attr_native_value
        self.async_write_ha_state()


class RainbirdSeasonalAdjustment(RainbirdExtendedControllerEntity, NumberEntity):
    """A program's seasonal adjustment: 100% runs zones as programmed."""

    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = SEASONAL_ADJUST_MIN
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator: RainbirdExtendedCoordinator, key: int) -> None:
        """Initialize the number."""
        unique_key, translation_key, placeholders = water_budget_naming(key)
        super().__init__(coordinator, unique_key)
        self._attr_translation_key = translation_key
        self._attr_translation_placeholders = placeholders
        self._key = key
        self._attr_native_max_value = coordinator.max_seasonal_adjust

    @property
    def available(self) -> bool:
        """Available once the controller has reported the value."""
        return super().available and self._key in self.coordinator.water_budgets

    @property
    def native_value(self) -> int | None:
        """Return the seasonal adjustment."""
        return self.coordinator.water_budgets.get(self._key)

    async def async_set_native_value(self, value: float) -> None:
        """Set the seasonal adjustment."""
        await self.coordinator.async_set_water_budget(self._key, round(value))
