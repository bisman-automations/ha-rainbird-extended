"""Valve runtime, flow rate and run interval per zone."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberMode, RestoreNumber
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import RainbirdExtendedConfigEntry
from .const import (
    DEFAULT_INTERVAL_DAYS,
    FLOW_RATE_MAX,
    FLOW_RATE_STEP,
    INTERVAL_MAX_DAYS,
    INTERVAL_MIN_DAYS,
    RUNTIME_MAX_SECONDS,
    RUNTIME_MIN_SECONDS,
    RUNTIME_STEP_SECONDS,
)
from .coordinator import RainbirdExtendedCoordinator
from .entity import RainbirdExtendedZoneEntity, water_units

# The device takes one command at a time.
PARALLEL_UPDATES = 1


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
            RainbirdZoneInterval(coordinator, zone),
        )
    ]
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

    @callback
    def async_entity_id_changed(self, old_entity_id: str) -> None:
        """Keep running when the entity ID is changed (Home Assistant 2026.11+).

        Nothing here is keyed on the entity ID, so the entity carries on as is.
        """
        parent = getattr(super(), "async_entity_id_changed", None)
        if parent is not None:
            parent(old_entity_id)

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

    @callback
    def async_entity_id_changed(self, old_entity_id: str) -> None:
        """Keep running when the entity ID is changed (Home Assistant 2026.11+).

        Nothing here is keyed on the entity ID, so the entity carries on as is.
        """
        parent = getattr(super(), "async_entity_id_changed", None)
        if parent is not None:
            parent(old_entity_id)

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


class RainbirdZoneInterval(RainbirdExtendedZoneEntity, RestoreNumber):
    """How many days apart the zone runs, for its estimated next run."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = INTERVAL_MIN_DAYS
    _attr_native_max_value = INTERVAL_MAX_DAYS
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.DAYS
    _attr_translation_key = "run_interval"

    def __init__(self, coordinator: RainbirdExtendedCoordinator, zone: int) -> None:
        """Initialize the interval number."""
        super().__init__(coordinator, zone, "run_interval")
        self._attr_native_value = DEFAULT_INTERVAL_DAYS

    @callback
    def async_entity_id_changed(self, old_entity_id: str) -> None:
        """Keep running when the entity ID is changed (Home Assistant 2026.11+).

        Nothing here is keyed on the entity ID, so the entity carries on as is.
        """
        parent = getattr(super(), "async_entity_id_changed", None)
        if parent is not None:
            parent(old_entity_id)

    async def async_added_to_hass(self) -> None:
        """Restore the interval and share it with the next run sensor."""
        await super().async_added_to_hass()
        if (
            last := await self.async_get_last_number_data()
        ) is not None and last.native_value is not None:
            self._attr_native_value = int(last.native_value)
        self.coordinator.intervals[self._zone] = int(self._attr_native_value or 1)

    async def async_set_native_value(self, value: float) -> None:
        """Set a new interval."""
        self._attr_native_value = int(value)
        self.coordinator.intervals[self._zone] = int(value)
        self.async_write_ha_state()
        self.coordinator.async_update_listeners()
