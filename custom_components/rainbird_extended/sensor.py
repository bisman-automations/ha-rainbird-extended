"""Sensors for Rain Bird Extended."""

from __future__ import annotations

from datetime import datetime, timedelta
import math
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from . import RainbirdExtendedConfigEntry
from .const import DEFAULT_INTERVAL_DAYS
from .coordinator import SOURCE_OTHER, RainbirdExtendedCoordinator, ZoneRun
from .entity import RainbirdExtendedZoneEntity, water_units

# Coordinator or push updates; nothing polls on its own.
PARALLEL_UPDATES = 0

ATTR_DURATION = "duration"
ATTR_END = "end"
ATTR_SOURCE = "source"
ATTR_LAST_START = "last_start"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RainbirdExtendedConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the zone and controller sensors."""
    coordinator = entry.runtime_data
    _, volume_unit = water_units(hass)
    entities: list[Entity] = []
    for zone in coordinator.linked_zones:
        entities.extend(
            (
                RainbirdZoneTimeRemaining(coordinator, zone),
                RainbirdZoneLastRun(coordinator, zone),
                RainbirdZoneWaterUsed(coordinator, zone, volume_unit),
            )
        )
        entities.append(RainbirdZoneNextRun(coordinator, zone))
    async_add_entities(entities)


class RainbirdZoneTimeRemaining(
    RainbirdExtendedZoneEntity, RestoreEntity, SensorEntity
):
    """When the current run of this zone ends, or the last one ended.

    A timestamp, so the frontend shows it as a live countdown ("in 4 minutes")
    and HomeKit can derive "Remaining Duration" from it (nothing left once it's
    in the past). While idle it's when the last run ended, kept across
    restarts, so it's only unknown before the zone's first run.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_translation_key = "time_remaining"

    def __init__(self, coordinator: RainbirdExtendedCoordinator, zone: int) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, zone, "time_remaining")
        self._last_end: datetime | None = None

    @callback
    def async_entity_id_changed(self, old_entity_id: str) -> None:
        """Keep running when the entity ID is changed (Home Assistant 2026.11+).

        Nothing here is keyed on the entity ID, so the entity carries on as is.
        """
        parent = getattr(super(), "async_entity_id_changed", None)
        if parent is not None:
            parent(old_entity_id)

    async def async_added_to_hass(self) -> None:
        """Restore when the last run ended."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            self._last_end = dt_util.parse_datetime(last.state)

    @property
    def native_value(self) -> datetime | None:
        """Return the expected end of the current run, or the last run's end."""
        if (end := (self.coordinator.data or {}).get(self._zone)) is not None:
            return end
        run = self.coordinator.last_runs.get(self._zone)
        if run is not None and run.end is not None:
            self._last_end = run.end
        if self._last_end is not None and self._last_end > dt_util.utcnow():
            # A run's expected end that it stopped before.
            return dt_util.utcnow()
        return self._last_end

    @property
    def extra_state_attributes(self) -> dict[str, bool]:
        """Whether the end is only estimated from the valve runtime."""
        return {"estimated": self._zone in self.coordinator.estimated_zones}


class RainbirdZoneNextRun(RainbirdExtendedZoneEntity, RestoreEntity, SensorEntity):
    """When the zone next runs.

    From the controller's schedule when it can be read. Otherwise estimated:
    the last run's start plus the zone's Run every interval, moved on by whole
    intervals if that's already passed (a skipped day). Before any run, the
    estimate counts from when it was first needed. Unknown while the
    controller is turned off (see the Controller power option).
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_translation_key = "next_run"

    def __init__(self, coordinator: RainbirdExtendedCoordinator, zone: int) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, zone, "next_run")
        self._last_start: datetime | None = None
        self._estimated = False

    @callback
    def async_entity_id_changed(self, old_entity_id: str) -> None:
        """Keep running when the entity ID is changed (Home Assistant 2026.11+).

        Nothing here is keyed on the entity ID, so the entity carries on as is.
        """
        parent = getattr(super(), "async_entity_id_changed", None)
        if parent is not None:
            parent(old_entity_id)

    async def async_added_to_hass(self) -> None:
        """Restore the start the estimate counts from."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None and (
            start := last.attributes.get(ATTR_LAST_START)
        ):
            self._last_start = dt_util.parse_datetime(str(start))

    @property
    def native_value(self) -> datetime | None:
        """Return when the zone next runs."""
        if self.coordinator.turned_off:
            # It won't run until the controller is turned back on.
            return None
        if (scheduled := self.coordinator.next_run(self._zone)) is not None:
            self._estimated = False
            return scheduled
        self._estimated = True
        now = dt_util.utcnow()
        if (run := self.coordinator.last_runs.get(self._zone)) is not None:
            self._last_start = run.start
        if self._last_start is None:
            self._last_start = now.replace(second=0, microsecond=0)
        interval = timedelta(
            days=self.coordinator.intervals.get(self._zone, DEFAULT_INTERVAL_DAYS)
        )
        nxt = self._last_start + interval
        if nxt <= now:
            nxt += interval * math.ceil((now - nxt) / interval + 1e-9)
        return nxt

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Whether it's estimated, and the start it counts from."""
        return {
            "estimated": self._estimated,
            ATTR_LAST_START: self._last_start.isoformat() if self._last_start else None,
            "controller_off": self.coordinator.turned_off,
        }


class RainbirdZoneLastRun(RainbirdExtendedZoneEntity, RestoreSensor):
    """When this zone last started running, and for how long it ran."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_translation_key = "last_run"

    def __init__(self, coordinator: RainbirdExtendedCoordinator, zone: int) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, zone, "last_run")
        self._restored: ZoneRun | None = None

    @callback
    def async_entity_id_changed(self, old_entity_id: str) -> None:
        """Keep running when the entity ID is changed (Home Assistant 2026.11+).

        Nothing here is keyed on the entity ID, so the entity carries on as is.
        """
        parent = getattr(super(), "async_entity_id_changed", None)
        if parent is not None:
            parent(old_entity_id)

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

    @callback
    def async_entity_id_changed(self, old_entity_id: str) -> None:
        """Keep running when the entity ID is changed (Home Assistant 2026.11+).

        Nothing here is keyed on the entity ID, so the entity carries on as is.
        """
        parent = getattr(super(), "async_entity_id_changed", None)
        if parent is not None:
            parent(old_entity_id)

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
