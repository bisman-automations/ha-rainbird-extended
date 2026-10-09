"""Sensors for Rain Bird Extended."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import RainbirdExtendedConfigEntry
from .coordinator import SOURCE_OTHER, RainbirdExtendedCoordinator, ZoneRun
from .entity import (
    RainbirdExtendedControllerEntity,
    RainbirdExtendedZoneEntity,
    water_budget_naming,
    water_units,
)

ATTR_DURATION = "duration"
ATTR_END = "end"
ATTR_SOURCE = "source"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RainbirdExtendedConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the zone and controller sensors."""
    coordinator = entry.runtime_data
    _, volume_unit = water_units(hass)
    entities: list[Entity] = []
    if not coordinator.supports_water_budget:
        # Read-only fallback from the controller state for older models.
        entities.append(RainbirdSeasonalAdjustment(coordinator))
    elif not coordinator.can_set_water_budget:
        # pyrainbird too old to change it: show it read-only.
        entities.extend(
            RainbirdWaterBudget(coordinator, key)
            for key in coordinator.water_budget_keys
        )
    for zone in coordinator.linked_zones:
        entities.extend(
            (
                RainbirdZoneTimeRemaining(coordinator, zone),
                RainbirdZoneLastRun(coordinator, zone),
                RainbirdZoneWaterUsed(coordinator, zone, volume_unit),
            )
        )
        if coordinator.schedule is not None and coordinator.max_programs:
            entities.append(RainbirdZoneNextRun(coordinator, zone))
    async_add_entities(entities)


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

    @property
    def extra_state_attributes(self) -> dict[str, bool]:
        """Whether the end is only estimated from the valve runtime."""
        return {"estimated": self._zone in self.coordinator.estimated_zones}


class RainbirdZoneNextRun(RainbirdExtendedZoneEntity, SensorEntity):
    """When the controller's schedule next runs this zone."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_translation_key = "next_run"

    def __init__(self, coordinator: RainbirdExtendedCoordinator, zone: int) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, zone, "next_run")

    @property
    def native_value(self) -> datetime | None:
        """Return the start of the next scheduled run."""
        return self.coordinator.next_run(self._zone)


class RainbirdZoneLastRun(RainbirdExtendedZoneEntity, RestoreSensor):
    """When this zone last started running, and for how long it ran."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_translation_key = "last_run"

    def __init__(self, coordinator: RainbirdExtendedCoordinator, zone: int) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, zone, "last_run")
        self._restored: ZoneRun | None = None

    async def async_added_to_hass(self) -> None:
        """Restore the last run from before a restart."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is None:
            return
        start = dt_util.parse_datetime(last.state)
        if start is None:
            return
        end = last.attributes.get(ATTR_END)
        self._restored = ZoneRun(
            start,
            dt_util.parse_datetime(end) if isinstance(end, str) else None,
            last.attributes.get(ATTR_SOURCE) or SOURCE_OTHER,
        )

    @property
    def _run(self) -> ZoneRun | None:
        return self.coordinator.last_runs.get(self._zone) or self._restored

    @property
    def native_value(self) -> datetime | None:
        """Return when the last run started."""
        return run.start if (run := self._run) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return when the last run ended and how long it lasted (seconds)."""
        run = self._run
        duration: timedelta | None = run.duration if run else None
        return {
            ATTR_END: run.end.isoformat() if run and run.end else None,
            ATTR_DURATION: round(duration.total_seconds()) if duration else None,
            ATTR_SOURCE: run.source if run else None,
        }


class RainbirdZoneWaterUsed(RainbirdExtendedZoneEntity, RestoreSensor):
    """Water used by the zone, from its run time and flow rate.

    A total that only increases, so it can be added to the Energy dashboard's
    water consumption.
    """

    _attr_device_class = SensorDeviceClass.WATER
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 1
    _attr_translation_key = "water_used"

    def __init__(
        self, coordinator: RainbirdExtendedCoordinator, zone: int, unit: str
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, zone, "water_used")
        self._attr_native_unit_of_measurement = unit
        self._attr_native_value = 0.0
        self._seen_seconds = coordinator.run_seconds.get(zone, 0.0)

    async def async_added_to_hass(self) -> None:
        """Restore the running total."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_sensor_data()) is not None and isinstance(
            last.native_value, (int, float)
        ):
            self._attr_native_value = float(last.native_value)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Add the water used since the last update."""
        seconds = self.coordinator.run_seconds.get(self._zone, 0.0)
        if (delta := seconds - self._seen_seconds) > 0:
            flow = self.coordinator.flow_rates.get(self._zone, 0.0)
            total = self._attr_native_value
            current = float(total) if isinstance(total, (int, float)) else 0.0
            self._attr_native_value = round(current + flow * delta / 60, 3)
        self._seen_seconds = seconds
        super()._handle_coordinator_update()


class RainbirdSeasonalAdjustment(RainbirdExtendedControllerEntity, SensorEntity):
    """The controller's seasonal adjustment (100% = runtimes as programmed)."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "seasonal_adjustment"

    def __init__(self, coordinator: RainbirdExtendedCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "seasonal_adjustment")

    @property
    def available(self) -> bool:
        """Only available for controllers that report it."""
        return (
            super().available
            and self.coordinator.supports_controller_state is not False
            and self.coordinator.controller_state is not None
        )

    @property
    def native_value(self) -> int | None:
        """Return the seasonal adjustment."""
        state = self.coordinator.controller_state
        return state.seasonal_adjust if state else None


class RainbirdWaterBudget(RainbirdExtendedControllerEntity, SensorEntity):
    """A program's seasonal adjustment, read-only."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: RainbirdExtendedCoordinator, key: int) -> None:
        """Initialize the sensor."""
        unique_key, translation_key, placeholders = water_budget_naming(key)
        super().__init__(coordinator, unique_key)
        self._attr_translation_key = translation_key
        self._attr_translation_placeholders = placeholders
        self._key = key

    @property
    def available(self) -> bool:
        """Available once the controller has reported the value."""
        return super().available and self._key in self.coordinator.water_budgets

    @property
    def native_value(self) -> int | None:
        """Return the seasonal adjustment."""
        return self.coordinator.water_budgets.get(self._key)
