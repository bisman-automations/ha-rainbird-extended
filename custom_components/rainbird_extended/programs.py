"""Programs set up in the options, for controllers whose schedule can't be read.

Some controllers (the ARC8, for one) don't share their programs over the local
API. Entering them here, as they're set in the Rain Bird app, gives the same
schedule the controller would report: upcoming runs, each zone's next run and
when a scheduled run ends.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import logging
from typing import Any

from ical.iter import SortableItem
from ical.timespan import Timespan
from pyrainbird.const import DayOfWeek, ProgramFrequency
from pyrainbird.timeline import ProgramEvent, ProgramId, create_recurrence

_LOGGER = logging.getLogger(__name__)

# Option keys, one dict per program: "program_a", "program_b", ...
PROGRAM_KEY = "program_{}"
CONF_PROGRAM_NAME = "name"
CONF_FREQUENCY = "frequency"
CONF_DAYS = "days"
CONF_EVERY_DAYS = "every_days"
CONF_START_DATE = "start_date"
CONF_START_TIMES = ("start_time_1", "start_time_2", "start_time_3", "start_time_4")
CONF_STATION_DELAY = "station_delay"
CONF_SEASONAL_ADJUST = "seasonal_adjust"
CONF_ZONES = "zones"

FREQUENCY_CUSTOM = "custom"
FREQUENCY_ODD = "odd"
FREQUENCY_EVEN = "even"
FREQUENCY_CYCLIC = "cyclic"
FREQUENCIES = {
    FREQUENCY_CUSTOM: ProgramFrequency.CUSTOM,
    FREQUENCY_ODD: ProgramFrequency.ODD,
    FREQUENCY_EVEN: ProgramFrequency.EVEN,
    FREQUENCY_CYCLIC: ProgramFrequency.CYCLIC,
}
# Day option values, Sunday first as in the Rain Bird app.
WEEKDAYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")

DEFAULT_EVERY_DAYS = 2
EVERY_DAYS_MAX = 30
STATION_DELAY_MAX = 3600
DEFAULT_SEASONAL_ADJUST = 100


def program_letter(program: int) -> str:
    """0 -> A."""
    return chr(ord("A") + program)


def program_key(program: int) -> str:
    """Options key of a program (0 -> program_a)."""
    return PROGRAM_KEY.format(program_letter(program).lower())


@dataclass(frozen=True)
class ConfiguredProgram:
    """A program as set in the options."""

    program: int
    name: str
    frequency: ProgramFrequency
    days: frozenset[DayOfWeek]
    every_days: int
    start_date: date | None
    starts: tuple[time, ...]
    zones: tuple[int, ...]
    station_delay: timedelta
    seasonal_adjust: int

    @property
    def title(self) -> str:
        """ "Program A" or "Program A: Normal Watering"."""
        letter = f"Program {program_letter(self.program)}"
        return f"{letter}: {self.name}" if self.name else letter

    def recurrence(
        self,
        start: time,
        now: datetime,
        offset: timedelta,
        duration: timedelta,
        *,
        zone: int | None = None,
        delay_days: int = 0,
    ) -> Iterable[SortableItem[Timespan, ProgramEvent]]:
        """Runs from one start time, shifted by offset and lasting duration.

        Like the controller's own schedule, as worked out by pyrainbird.
        """
        dtstart = now.replace(
            hour=start.hour, minute=start.minute, second=0, microsecond=0
        )
        synchro = 0
        first = self.start_date
        if self.frequency == ProgramFrequency.CYCLIC:
            if first is None:
                first = now.date()
            synchro = (first - now.date()).days % self.every_days
        items = create_recurrence(
            ProgramId(self.program, zone),
            self.frequency,
            dtstart + offset,
            duration,
            synchro,
            set(self.days),
            self.every_days,
            delay_days=delay_days,
        )
        return items if first is None else _From(items, first)


class _From(Iterable[SortableItem[Timespan, ProgramEvent]]):
    """Only the runs from a program's start date on."""

    def __init__(
        self, items: Iterable[SortableItem[Timespan, ProgramEvent]], first: date
    ) -> None:
        self._items = items
        self._first = first

    def __iter__(self) -> Iterator[SortableItem[Timespan, ProgramEvent]]:
        for item in self._items:
            if item.key.start.date() >= self._first:
                yield item


def _time(value: Any) -> time | None:
    if isinstance(value, str) and value:
        try:
            return time.fromisoformat(value).replace(second=0, microsecond=0)
        except ValueError:
            return None
    return None


def parse_program(program: int, data: dict[str, Any]) -> ConfiguredProgram | None:
    """A program from its options, or None if it's off (no zones or start times)."""
    zones = tuple(sorted({int(z) for z in data.get(CONF_ZONES) or []}))
    starts = tuple(
        sorted({t for key in CONF_START_TIMES if (t := _time(data.get(key)))})
    )
    if not zones or not starts:
        return None
    frequency = FREQUENCIES.get(data.get(CONF_FREQUENCY, ""), ProgramFrequency.CUSTOM)
    days = frozenset(
        DayOfWeek(WEEKDAYS.index(day))
        for day in data.get(CONF_DAYS, WEEKDAYS)
        if day in WEEKDAYS
    )
    if frequency == ProgramFrequency.CUSTOM and not days:
        return None
    start_date = None
    if raw := data.get(CONF_START_DATE):
        try:
            start_date = date.fromisoformat(str(raw))
        except ValueError:
            _LOGGER.debug("Ignoring start date %s of program %s", raw, program)
    return ConfiguredProgram(
        program=program,
        name=str(data.get(CONF_PROGRAM_NAME) or "").strip(),
        frequency=frequency,
        days=days,
        every_days=max(1, int(data.get(CONF_EVERY_DAYS) or DEFAULT_EVERY_DAYS)),
        start_date=start_date,
        starts=starts,
        zones=zones,
        station_delay=timedelta(seconds=int(data.get(CONF_STATION_DELAY) or 0)),
        seasonal_adjust=int(data.get(CONF_SEASONAL_ADJUST) or DEFAULT_SEASONAL_ADJUST),
    )


def parse_programs(options: dict[str, Any], count: int) -> list[ConfiguredProgram]:
    """Every program that's set up in the options."""
    programs = []
    for index in range(count):
        data = options.get(program_key(index))
        if isinstance(data, dict) and (program := parse_program(index, data)):
            programs.append(program)
    return programs
