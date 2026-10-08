"""Run history: every zone run as a calendar event."""

from __future__ import annotations

from datetime import datetime, timedelta
from functools import partial
import logging
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import RainbirdExtendedConfigEntry
from .const import DOMAIN
from .coordinator import (
    EVENT_FINISHED,
    SOURCE_BLOWOUT,
    SOURCE_HOME_ASSISTANT,
    SOURCE_OTHER,
    SOURCE_PROGRAM,
    SOURCE_RUN_ALL_ZONES,
    SOURCE_SCHEDULE,
    RainbirdExtendedCoordinator,
    ZoneRun,
)
from .entity import RainbirdExtendedControllerEntity, find_device

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
# Runs older than this are dropped.
KEEP = timedelta(days=366)
SAVE_DELAY = 30

SOURCE_TEXT = {
    SOURCE_HOME_ASSISTANT: "Home Assistant",
    SOURCE_RUN_ALL_ZONES: "Run all zones",
    SOURCE_BLOWOUT: "Blowout",
    SOURCE_PROGRAM: "a program",
    SOURCE_SCHEDULE: "the schedule",
    SOURCE_OTHER: "the controller or Rain Bird app",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RainbirdExtendedConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the run history calendar."""
    async_add_entities([RainbirdRunHistory(entry.runtime_data)])


class RainbirdRunHistory(RainbirdExtendedControllerEntity, CalendarEntity):
    """Past runs of every zone, with how long and what started them."""

    _attr_translation_key = "run_history"

    def __init__(self, coordinator: RainbirdExtendedCoordinator) -> None:
        """Initialize the calendar."""
        super().__init__(coordinator, "run_history")
        self._store: Store[dict[str, Any]] = Store(
            coordinator.hass,
            STORAGE_VERSION,
            f"{DOMAIN}.{coordinator.config_entry.entry_id}.run_history",
        )
        # {"zone", "start", "end", "source"}, oldest first.
        self._runs: list[dict[str, Any]] = []

    async def async_added_to_hass(self) -> None:
        """Load the history and record each run as it finishes."""
        await super().async_added_to_hass()
        if (data := await self._store.async_load()) is not None:
            self._runs = list(data.get("runs", []))
        for zone in self.coordinator.linked_zones:
            self.async_on_remove(
                self.coordinator.async_add_run_listener(
                    zone, partial(self._handle_run, zone)
                )
            )

    @callback
    def _handle_run(self, zone: int, event: str, run: ZoneRun) -> None:
        if event == EVENT_FINISHED and run.end is not None:
            self._runs.append(
                {
                    "zone": zone,
                    "start": run.start.isoformat(),
                    "end": run.end.isoformat(),
                    "source": run.source,
                }
            )
            cutoff = (dt_util.utcnow() - KEEP).isoformat()
            self._runs = [r for r in self._runs if r["end"] >= cutoff]
            self._store.async_delay_save(lambda: {"runs": self._runs}, SAVE_DELAY)
        self.async_write_ha_state()

    def _zone_name(self, zone: int) -> str:
        device = find_device(
            self.hass,
            self.coordinator.rainbird_entry.entry_id,
            f"{self.coordinator.rainbird_unique_id}-{zone}",
        )
        if device is not None and (name := device.name_by_user or device.name):
            return name
        return f"Zone {zone}"

    def _event(
        self, zone: int, start: datetime, end: datetime, source: str, running: bool
    ) -> CalendarEvent:
        # The controller is polled once a minute, so a run's recorded end can
        # be at (or, for a stale expected end, before) its start. Calendar
        # events must have a duration: show such runs as a minute long.
        end = max(end, start + timedelta(minutes=1))
        minutes = max(1, round((end - start).total_seconds() / 60))
        started_by = SOURCE_TEXT.get(source, source)
        description = (
            f"Running, started by {started_by}"
            if running
            else f"{minutes} min, started by {started_by}"
        )
        return CalendarEvent(
            summary=self._zone_name(zone),
            start=dt_util.as_local(start),
            end=dt_util.as_local(end),
            description=description,
            uid=f"{zone}-{start.isoformat()}",
        )

    def _all_events(self) -> list[CalendarEvent]:
        events = []
        for record in self._runs:
            try:
                start = dt_util.parse_datetime(record["start"])
                end = dt_util.parse_datetime(record["end"])
                if start and end:
                    events.append(
                        self._event(record["zone"], start, end, record["source"], False)
                    )
            except (KeyError, TypeError, ValueError, HomeAssistantError) as err:
                _LOGGER.debug("Skipping run history record %s: %s", record, err)
        # Runs still going, ending when expected (or now, if unknown).
        now = dt_util.utcnow()
        end_times = self.coordinator.data or {}
        for zone, run in self.coordinator.last_runs.items():
            if run.end is None:
                end = end_times.get(zone) or now + timedelta(minutes=1)
                events.append(self._event(zone, run.start, end, run.source, True))
        return events

    @property
    def event(self) -> CalendarEvent | None:
        """The run going on now, or else the most recent one."""
        events = self._all_events()
        return max(events, key=lambda e: e.start) if events else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Runs overlapping the requested range."""
        return sorted(
            (
                event
                for event in self._all_events()
                if event.end_datetime_local > start_date
                and event.start_datetime_local < end_date
            ),
            key=lambda e: e.start,
        )
