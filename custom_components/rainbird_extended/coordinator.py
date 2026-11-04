"""Coordinator tracking per-zone run times for Rain Bird Extended.

Commands and readings go through :class:`RainbirdCore`: Rain Bird's entities
and services. Optimistic end times live on this coordinator and on our own
entities. Nothing here writes Rain Bird's coordinator data or entity registry.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import math

from ical.iter import MergedIterable, SortableItem
from ical.timespan import Timespan
from pyrainbird.timeline import ProgramEvent, ProgramTimeline

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_STATE_CHANGED, STATE_OFF
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import (
    async_track_point_in_utc_time,
    async_track_state_change_event,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BLOWOUT_CYCLES,
    CONF_BLOWOUT_ON_MINUTES,
    CONF_BLOWOUT_REST_SECONDS,
    CONF_BLOWOUT_ZONES,
    CONF_CYCLE_MINUTES,
    CONF_POWER_ENTITY,
    CONF_RUN_ALL_ZONES,
    CONF_SOAK_MINUTES,
    CONFIRM_DELAY,
    DEFAULT_BLOWOUT_CYCLES,
    DEFAULT_BLOWOUT_ON_MINUTES,
    DEFAULT_BLOWOUT_REST_SECONDS,
    DEFAULT_SOAK_MINUTES,
    DOMAIN,
    EARLY_STOP_MARGIN,
    END_TIME_TOLERANCE,
    SCHEDULE_TOLERANCE,
    START_GRACE_PERIOD,
)
from .core_api import RainbirdCore, ZonePattern, calendar_zone_runs
from .programs import ConfiguredProgram, parse_programs
from .sequence import Step, plan_blowout, plan_steps

_LOGGER = logging.getLogger(__name__)


SOURCE_HOME_ASSISTANT = "home_assistant"
SOURCE_RUN_ALL_ZONES = "run_all_zones"
SOURCE_BLOWOUT = "blowout"
SOURCE_PROGRAM = "program"
SOURCE_SCHEDULE = "schedule"
SOURCE_OTHER = "other"

EVENT_STARTED = "started"
EVENT_FINISHED = "finished"


@dataclass
class _LocalRun:
    """A run started from Home Assistant."""

    end: datetime
    started_at: datetime
    seen_active: bool = False


@dataclass
class ZoneRun:
    """The current or most recent run of a zone, however it was started."""

    start: datetime
    end: datetime | None = None
    # home_assistant, run_all_zones, program, schedule or other
    source: str = SOURCE_OTHER

    @property
    def duration(self) -> timedelta | None:
        """How long the run lasted (None while it is still running)."""
        return None if self.end is None else self.end - self.start


type RunListener = Callable[[str, ZoneRun], None]

# Time remaining for a zone still running past its estimated end.
OVERRUN_ESTIMATE = timedelta(minutes=1)

# While a zone is running, re-read the switches about as often as Rain Bird
# polls. A switch that stays on does not fire a state change.
RUNNING_REFRESH = timedelta(seconds=60)

# Shortest run recorded (the controller's states are only seen once a minute).
MIN_RUN = timedelta(seconds=1)

# zone number -> when the current run is expected to end (None if unknown)
type ZoneEndTimes = dict[int, datetime | None]


class RainbirdExtendedCoordinator(DataUpdateCoordinator[ZoneEndTimes]):
    """Compute when each running zone will finish, and track runs.

    This coordinator does not poll the controller. It refreshes when a Rain Bird
    entity changes state (the zone switches update about once a minute) and
    after a command from here. End times come from a run started here, from
    programs entered in the options, from zone run times on Rain Bird's
    schedule calendar, or from the valve runtime.
    """

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        rainbird_entry: ConfigEntry,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN} {rainbird_entry.title}",
            update_interval=None,
            request_refresh_debouncer=Debouncer(
                hass, _LOGGER, cooldown=2, immediate=True
            ),
        )
        self.rainbird_entry = rainbird_entry
        # Public entities and services only. Never the core runtime data.
        self.core = RainbirdCore(hass, rainbird_entry)
        # (program index, local start) of a program started from here.
        self._manual_program: tuple[int, datetime] | None = None
        # Programs set up in the options; when there are any they're used
        # instead of the controller's schedule (which some can't share).
        self.programs: list[ConfiguredProgram] = parse_programs(
            dict(config_entry.options), self.max_programs
        )
        # On/off entity that says whether the controller is turned on.
        self.power_entity: str | None = config_entry.options.get(CONF_POWER_ENTITY)
        # Days we just set, until the Rain delay number catches up.
        self._pending_rain_delay: int | None = None
        # Zone windows and program patterns from the schedule calendar.
        self._calendar_runs: list[tuple[int, datetime, datetime, int | None]] = []
        self._program_patterns: dict[int, list[ZonePattern]] = {}
        self._logged_calendar_without_zones = False
        # Runs started from Home Assistant, by zone.
        self._started: dict[int, _LocalRun] = {}
        # Zones whose end time is only an estimate from their valve runtime.
        self.estimated_zones: set[int] = set()
        # zone -> days between runs, kept in sync by the number entities.
        self.intervals: dict[int, int] = {}
        # zone -> runtime in seconds, kept in sync by the number entities.
        self.runtimes: dict[int, int] = {}
        # zone -> flow rate per minute, kept in sync by the number entities.
        self.flow_rates: dict[int, float] = {}
        # Zones whose core Rain Bird device exists, set up once.
        self.linked_zones: list[int] = []
        # Run tracking (any source).
        self.last_runs: dict[int, ZoneRun] = {}
        # zone -> seconds the zone has run since Home Assistant started.
        self.run_seconds: dict[int, float] = {}
        self._prev_running: set[int] = set()
        self._prev_end_times: ZoneEndTimes = {}
        self._last_tick: datetime | None = None
        # Sequence of steps (Run all zones, cycle and soak).
        self._steps: list[Step] = []
        self._sequence_active = False
        self._sequence_source = SOURCE_RUN_ALL_ZONES
        self._sequence_zone: int | None = None
        # The step in progress and when it ends (to pause partway through).
        self._current_step: Step | None = None
        self._step_end: datetime | None = None
        # A paused sequence: the steps left, and what started it.
        self._paused_steps: list[Step] | None = None
        self._paused_source = SOURCE_RUN_ALL_ZONES
        self._sequence_unsub: CALLBACK_TYPE | None = None
        # Re-read the switches while a run or sequence is in progress.
        self._running_unsub: CALLBACK_TYPE | None = None
        # zone -> callbacks for run started/finished.
        self._run_listeners: dict[int, list[RunListener]] = {}

    @property
    def rainbird_unique_id(self) -> str | None:
        """Unique id of the core Rain Bird config entry (formatted MAC)."""
        return self.rainbird_entry.unique_id

    @property
    def max_programs(self) -> int:
        """How many programs the options can offer.

        Rain Bird's Run program buttons (Home Assistant 2026.10 and newer)
        say exactly. Otherwise a schedule calendar means the controller has
        programs, and the options offer A-D. No calendar means no programs.
        """
        from .config_flow import MAX_PROGRAMS  # noqa: PLC0415

        buttons = self.core.program_buttons()
        if buttons:
            return min(max(buttons) + 1, MAX_PROGRAMS)
        if self.core.calendar_entity_id() is not None:
            return MAX_PROGRAMS
        return 0

    @property
    def zones(self) -> set[int]:
        """Zones Rain Bird has a switch for."""
        return self.core.zones()

    @property
    def local_runs(self) -> dict[int, _LocalRun]:
        """Runs started from Home Assistant that are still being tracked."""
        return dict(self._started)

    @property
    def sequence_running(self) -> bool:
        """Whether "Run all zones" (or a cycle and soak run) is in progress."""
        return self._sequence_active

    @callback
    def async_add_run_listener(self, zone: int, listener: RunListener) -> CALLBACK_TYPE:
        """Call listener(event, run) when a zone starts or finishes running."""
        self._run_listeners.setdefault(zone, []).append(listener)

        @callback
        def remove() -> None:
            self._run_listeners[zone].remove(listener)

        return remove

    def linkable_zones(self) -> list[int]:
        """Zones whose core Rain Bird device exists, in order."""
        from .entity import find_device  # noqa: PLC0415

        zones = []
        for zone in sorted(self.zones):
            if find_device(
                self.hass,
                self.rainbird_entry.entry_id,
                f"{self.rainbird_unique_id}-{zone}",
            ):
                zones.append(zone)
            else:
                _LOGGER.warning("No Rain Bird device found for zone %s; skipping", zone)
        return zones

    @property
    def active_zones(self) -> set[int]:
        """Zones running: the Rain Bird switches, plus a run we just started.

        The switch lags the command by a few seconds. The valve and Irrigating
        sensor follow this set, which stays on our coordinator.
        """
        zones = self.core.active_zones()
        now = dt_util.utcnow()
        for zone, run in self._started.items():
            if now < run.end:
                zones.add(zone)
        return zones

    @property
    def default_runtime(self) -> int:
        """Default runtime in seconds, from Rain Bird's duration option."""
        return self.core.default_duration_minutes * 60

    def runtime_for(self, zone: int) -> int:
        """Return the configured runtime for a zone in seconds."""
        return self.runtimes.get(zone, self.default_runtime)

    @callback
    def async_start(self) -> None:
        """Refresh when Rain Bird's entities change. Don't subscribe to its coordinators."""
        self.config_entry.async_on_unload(
            self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._handle_core_state)
        )
        self.config_entry.async_on_unload(
            self.hass.bus.async_listen(
                er.EVENT_ENTITY_REGISTRY_UPDATED, self._handle_registry
            )
        )
        if self.power_entity:
            # Turning the controller off or on changes what's scheduled.
            self.config_entry.async_on_unload(
                async_track_state_change_event(
                    self.hass, self.power_entity, self._handle_power_change
                )
            )
        self.config_entry.async_on_unload(self._cancel_sequence)
        self.config_entry.async_on_unload(self._cancel_running_refresh)

    @callback
    def _handle_power_change(self, _event: Event[EventStateChangedData]) -> None:
        self.async_update_listeners()

    @property
    def turned_off_since(self) -> datetime | None:
        """When the controller was turned off, or None while it's on.

        Only an explicit "off" from the power entity counts; unknown or
        unavailable doesn't, so a missing entity never hides the schedule.
        """
        if not self.power_entity:
            return None
        state = self.hass.states.get(self.power_entity)
        if state is None or state.state != STATE_OFF:
            return None
        return state.last_changed

    @property
    def turned_off(self) -> bool:
        """Whether the controller is turned off."""
        return self.turned_off_since is not None

    @callback
    def _handle_core_state(self, event: Event[EventStateChangedData]) -> None:
        """Refresh when a Rain Bird entity we watch changes."""
        if event.data["entity_id"] in self.core.watched_entity_ids():
            self.config_entry.async_create_background_task(
                self.hass, self.async_request_refresh(), f"{DOMAIN} refresh"
            )

    @callback
    def _handle_registry(self, event: Event[er.EventEntityRegistryUpdatedData]) -> None:
        """Pick up a Rain Bird switch that was enabled or added."""
        if event.data["action"] not in ("create", "update"):
            return
        if event.data["entity_id"] in self.core.watched_entity_ids():
            self.config_entry.async_create_background_task(
                self.hass, self.async_request_refresh(), f"{DOMAIN} refresh"
            )

    async def _async_update_data(self) -> ZoneEndTimes:
        """Work out the expected end time for each running zone."""
        await self._async_refresh_calendar()
        # Switches only. A run we just started is in ``_started`` until the
        # switch agrees, and is not treated as confirmation of itself.
        reported = self.core.active_zones()
        end_times = self._compute_end_times(reported)
        self._track_runs(end_times)
        self._schedule_running_refresh(bool(end_times) or self._sequence_active)
        return end_times

    async def _async_refresh_calendar(self) -> None:
        """Read Rain Bird's schedule calendar, when it has one."""
        if self.programs or self.core.calendar_entity_id() is None:
            if self.programs:
                self._calendar_runs = []
                self._program_patterns = {}
            return
        now = dt_util.now()
        events = await self.core.async_calendar_events(
            now - timedelta(hours=6), now + timedelta(days=31)
        )
        if events is None:
            return
        runs, patterns = calendar_zone_runs(events)
        self._calendar_runs = runs
        self._program_patterns = patterns
        if events and not runs and not self._logged_calendar_without_zones:
            self._logged_calendar_without_zones = True
            _LOGGER.info(
                "Rain Bird's schedule calendar doesn't list zone run times, "
                "so each zone's next run uses programs from the options or "
                "Run every. Zone run times are included on the calendar in "
                "Home Assistant 2026.11 and newer"
            )

    def _compute_end_times(self, active: set[int]) -> ZoneEndTimes:
        """Combine locally tracked end times with what the switches report.

        ``active`` is zones whose Rain Bird switch is on. It is not our
        optimistic set: a switch has to actually be on before a run counts
        as confirmed.
        """
        now = dt_util.utcnow()
        previous = self.data or {}

        # Forget runs that have stopped. A freshly started run gets a grace
        # period because the switch is slow to turn on, but once it has been
        # seen on, turning off means the run is finished.
        for zone, run in list(self._started.items()):
            if zone in active:
                if now - run.started_at > CONFIRM_DELAY:
                    run.seen_active = True
            elif run.seen_active or now - run.started_at > START_GRACE_PERIOD:
                del self._started[zone]
                if zone == self._sequence_zone and now < run.end - EARLY_STOP_MARGIN:
                    _LOGGER.debug("Zone %s stopped early; ending run all zones", zone)
                    self._cancel_sequence()

        self.estimated_zones = set()
        return {
            zone: self._end_time(zone, zone in active, previous.get(zone), now=now)
            for zone in active | set(self._started)
        }

    def _end_time(
        self,
        zone: int,
        active: bool,
        previous: datetime | None,
        *,
        now: datetime,
    ) -> datetime | None:
        """Pick the best known end time for one zone.

        Rain Bird doesn't publish remaining run time on any entity, so a run
        we didn't start is the schedule when we can see it, otherwise the
        valve runtime.
        """
        end = self._started[zone].end if zone in self._started else None
        if end is None and active:
            end = self.scheduled_end(zone)
        if end is None and active:
            # Started some other way (the Rain Bird app, or its own switch):
            # assume it runs for its valve runtime from when it was seen.
            run = self.last_runs.get(zone)
            start = run.start if run is not None and run.end is None else now
            estimate = start + timedelta(seconds=self.runtime_for(zone))
            if estimate <= now:
                # Still running past the estimate: about a minute left,
                # until it's seen stopping.
                estimate = now + OVERRUN_ESTIMATE
            self.estimated_zones.add(zone)
            end = estimate
        if (
            end is not None
            and previous is not None
            and abs(end - previous) < END_TIME_TOLERANCE
        ):
            end = previous
        return end

    def _track_runs(self, end_times: ZoneEndTimes) -> None:
        """Record run starts, stops and time spent running, from any source."""
        now = dt_util.utcnow()
        running = set(end_times)
        if self._last_tick is not None:
            for zone in self._prev_running:
                until = now
                if zone not in running:
                    # Stopped since the last update. If it was due to end in
                    # between, it ran until then rather than until now. An
                    # expected end from before the run started is stale (for
                    # example a schedule slot it started late for): ignore it.
                    # The switch can stay on a few seconds past that end; the
                    # recorded finish is the expected end, not the late poll.
                    run = self.last_runs.get(zone)
                    prev_end = self._prev_end_times.get(zone)
                    finished = now
                    if (
                        prev_end is not None
                        and prev_end < now
                        and (run is None or prev_end > run.start)
                    ):
                        finished = prev_end
                        until = (
                            prev_end if self._last_tick <= prev_end else self._last_tick
                        )
                    if run is not None and run.end is None:
                        run.end = max(finished, run.start + MIN_RUN)
                        self._fire_run_event(zone, EVENT_FINISHED, run)
                else:
                    # A run we started has a known end. The zone switch stays
                    # on for a few seconds after that, so don't count water
                    # past the end until it's clearly still running.
                    local = self._started.get(zone)
                    if (
                        local is not None
                        and local.end < now < local.end + START_GRACE_PERIOD
                    ):
                        until = max(local.end, self._last_tick)
                self.run_seconds[zone] = (
                    self.run_seconds.get(zone, 0.0)
                    + (until - self._last_tick).total_seconds()
                )
        for zone in running - self._prev_running:
            local = self._started.get(zone)
            run = ZoneRun(local.started_at if local else now, source=self._source(zone))
            self.last_runs[zone] = run
            self._fire_run_event(zone, EVENT_STARTED, run)
        self._prev_running = running
        self._prev_end_times = dict(end_times)
        self._last_tick = now

    def _source(self, zone: int) -> str:
        """Work out what started a zone that just began running."""
        if zone in self._started:
            if self._sequence_active and zone == self._sequence_zone:
                return self._sequence_source
            return SOURCE_HOME_ASSISTANT
        if self._manual_program is not None and self._slot(
            self._manual_program[0], zone
        ):
            return SOURCE_PROGRAM
        if self.scheduled_end(zone) is not None:
            return SOURCE_SCHEDULE
        return SOURCE_OTHER

    def _fire_run_event(self, zone: int, event: str, run: ZoneRun) -> None:
        for listener in list(self._run_listeners.get(zone, [])):
            # One listener failing must not stop run tracking or the others.
            try:
                listener(event, run)
            except Exception:
                _LOGGER.exception("Error handling %s run of zone %s", event, zone)

    def _budget_scale(self, configured: ConfiguredProgram) -> float:
        """How the program's own seasonal adjust stretches its runtimes.

        The controller's seasonal adjustment isn't available through Rain
        Bird's entities or services, so only the value entered with the
        program is used, and only for that program.
        """
        return (configured.seasonal_adjust or 100) / 100

    @property
    def rain_delay_days(self) -> int:
        """Days of rain delay still set.

        A value just sent from here wins until the Rain delay number reports
        it. Rain Bird updates that number on its next poll.
        """
        pending = self._pending_rain_delay
        reported = self.core.rain_delay_days()
        if pending is None:
            return reported
        if reported == pending:
            self._pending_rain_delay = None
            return reported
        return pending

    def _configured_slot(
        self, program: ConfiguredProgram, zone: int
    ) -> tuple[timedelta, timedelta] | None:
        """Offset and duration of a zone's run in a program from the options.

        Its zones run one after another, in zone order, each for its valve
        runtime scaled by seasonal adjust, with the station delay between them.
        """
        scale = self._budget_scale(program)
        offset = timedelta()
        for program_zone in program.zones:
            duration = timedelta(seconds=self.runtime_for(program_zone) * scale)
            if program_zone == zone:
                return offset, duration
            offset += duration + program.station_delay
        return None

    def program_length(self, program: ConfiguredProgram) -> timedelta:
        """How long a program from the options runs, start to finish."""
        last = self._configured_slot(program, program.zones[-1])
        return last[0] + last[1] if last else timedelta()

    def _slot(self, index: int, zone: int) -> tuple[timedelta, timedelta] | None:
        """A zone's slot in a program, from the options or the calendar."""
        if self.programs:
            configured = next((p for p in self.programs if p.program == index), None)
            return self._configured_slot(configured, zone) if configured else None
        for program_zone, offset, duration in self._program_patterns.get(index, []):
            if program_zone == zone and duration:
                return offset, duration
        return None

    def program_timeline(self, now: datetime) -> ProgramTimeline | None:
        """Every run of the programs set up in the options, whole programs."""
        delay = self.rain_delay_days
        iters: list[Iterable[SortableItem[Timespan, ProgramEvent]]] = [
            program.recurrence(
                start, now, timedelta(), self.program_length(program), delay_days=delay
            )
            for program in self.programs
            for start in program.starts
        ]
        return ProgramTimeline(MergedIterable(iters)) if iters else None

    def _zone_timeline(self, zone: int, now: datetime) -> ProgramTimeline | None:
        """Every scheduled run of a zone from the programs in the options."""
        if not self.programs:
            return None
        iters: list[Iterable[SortableItem[Timespan, ProgramEvent]]] = []
        delay = self.rain_delay_days
        for configured in self.programs:
            if (slot := self._configured_slot(configured, zone)) is None:
                continue
            offset, duration = slot
            iters.extend(
                configured.recurrence(
                    start, now, offset, duration, zone=zone, delay_days=delay
                )
                for start in configured.starts
            )
        return ProgramTimeline(MergedIterable(iters)) if iters else None

    def _calendar_next(self, zone: int, now: datetime) -> datetime | None:
        """Next start of a zone from Rain Bird's calendar, if it listed zones."""
        upcoming = [
            start
            for program_zone, start, _end, _program in self._calendar_runs
            if program_zone == zone and start > now
        ]
        return min(upcoming) if upcoming else None

    def _calendar_end(self, zone: int, now: datetime) -> datetime | None:
        """End of the calendar run this zone is in the middle of."""
        for program_zone, start, end, _program in self._calendar_runs:
            if program_zone != zone:
                continue
            if start - SCHEDULE_TOLERANCE <= now <= end + SCHEDULE_TOLERANCE:
                return end
        return None

    def next_run(self, zone: int) -> datetime | None:
        """When the schedule next runs this zone (None while turned off)."""
        if self.turned_off:
            return None
        now = dt_util.now()
        if self.programs:
            if (timeline := self._zone_timeline(zone, now)) is None:
                return None
            event = next(timeline.start_after(now), None)
            return dt_util.as_utc(event.start) if event else None
        start = self._calendar_next(zone, now)
        return dt_util.as_utc(start) if start else None

    def scheduled_end(self, zone: int) -> datetime | None:
        """When a running zone should finish, from a program or the calendar.

        Rain Bird doesn't publish remaining seconds. A program started from
        here uses the zone's slot. A run already on the calendar uses that
        event. Anything else is estimated from the valve runtime.
        """
        now = dt_util.now()
        if self._manual_program is not None:
            index, started = self._manual_program
            if slot := self._slot(index, zone):
                offset, duration = slot
                if started + offset - SCHEDULE_TOLERANCE <= now:
                    end = started + offset + duration
                    if now < end + SCHEDULE_TOLERANCE:
                        return dt_util.as_utc(end)
        if self.programs:
            if (timeline := self._zone_timeline(zone, now)) is None:
                return None
            event = next(timeline.active_after(now - SCHEDULE_TOLERANCE), None)
            if event is not None and event.start - SCHEDULE_TOLERANCE <= now:
                return dt_util.as_utc(event.end)
            return None
        calendar_end = self._calendar_end(zone, now)
        return dt_util.as_utc(calendar_end) if calendar_end else None

    async def async_start_zone(
        self, zone: int, seconds: int | None = None, *, _sequence: bool = False
    ) -> None:
        """Run a zone for its configured runtime (or the given seconds)."""
        if not _sequence:
            self._cancel_sequence()
        self._manual_program = None
        seconds = self.runtime_for(zone) if seconds is None else seconds
        minutes = max(1, round(seconds / 60))
        now = dt_util.utcnow()
        # Record the run before the service call. Turning the switch on
        # refreshes us while the call is still in progress.
        self._started = {zone: _LocalRun(now + timedelta(minutes=minutes), now)}
        try:
            await self.core.async_start_zone(zone, minutes)
        except HomeAssistantError:
            self._started.pop(zone, None)
            self._publish_end_times()
            raise
        self._publish_end_times()

    @property
    def paused(self) -> bool:
        """Whether a sequence is paused, waiting to be resumed."""
        return self._paused_steps is not None

    @property
    def paused_source(self) -> str | None:
        """What started the paused sequence (run_all_zones, blowout, ...)."""
        return self._paused_source if self.paused else None

    async def async_pause(self) -> None:
        """Pause Run all zones, cycle and soak or a blowout where it is."""
        if not self._sequence_active:
            raise HomeAssistantError("Nothing to pause")
        now = dt_util.utcnow()
        steps = list(self._steps)
        step, end = self._current_step, self._step_end
        if step is not None and end is not None and end > now:
            left = math.ceil((end - now).total_seconds())
            if step.zone is not None:
                # Rain Bird runs whole minutes: round what's left up.
                left = max(60, math.ceil(left / 60) * 60)
            steps.insert(0, Step(step.zone, left))
        source = self._sequence_source
        self._cancel_sequence()
        await self.core.async_stop()
        self._started.clear()
        self._paused_steps = steps
        self._paused_source = source
        _LOGGER.info("Paused %s with %s steps left", source, len(steps))
        self._publish_end_times()

    async def async_resume(self) -> None:
        """Carry on with a paused sequence."""
        if self._paused_steps is None:
            raise HomeAssistantError("Nothing is paused")
        steps, source = self._paused_steps, self._paused_source
        self._paused_steps = None
        if not steps:
            self.async_update_listeners()
            return
        await self._async_run_steps(steps, source)

    async def async_stop(self) -> None:
        """Stop all irrigation (Rain Bird can't stop a single zone)."""
        self._cancel_sequence()
        self._paused_steps = None
        self._manual_program = None
        await self.core.async_stop()
        self._started.clear()
        self._publish_end_times()

    async def async_run_program(self, program: int) -> None:
        """Press Rain Bird's Run program button (0 = A)."""
        self._cancel_sequence()
        await self.core.async_run_program(program)
        self._started.clear()
        self._manual_program = (program, dt_util.now())
        self._publish_end_times()

    async def async_set_rain_delay(
        self, days: int, *, wait_for_refresh: bool = False
    ) -> None:
        """Set the rain delay through Rain Bird's number or service.

        The number updates when Rain Bird next polls. Until then the value
        just set is what schedules use. ``wait_for_refresh`` gives that poll
        a short chance to land before returning; it does not ask Rain Bird
        to reload.
        """
        await self.core.async_set_rain_delay(days)
        self._pending_rain_delay = days
        if wait_for_refresh:
            for _ in range(20):
                if self.core.rain_delay_days() == days:
                    self._pending_rain_delay = None
                    break
                await asyncio.sleep(0.1)
        self.async_update_listeners()

    def run_all_zones_order(self) -> list[int]:
        """Zones Run all zones runs, in order (from the options, else all)."""
        configured = self.config_entry.options.get(CONF_RUN_ALL_ZONES)
        if not configured:
            return list(self.linked_zones)
        zones = [zone for zone in configured if zone in self.linked_zones]
        if skipped := [zone for zone in configured if zone not in self.linked_zones]:
            _LOGGER.warning("Run all zones: skipping unknown zones %s", skipped)
        return zones

    def _cycle_soak(self) -> tuple[int, int]:
        options = self.config_entry.options
        return (
            int(options.get(CONF_CYCLE_MINUTES, 0)) * 60,
            int(options.get(CONF_SOAK_MINUTES, DEFAULT_SOAK_MINUTES)) * 60,
        )

    async def async_run_all_zones(self) -> None:
        """Run the zones once, in order, each for its valve runtime."""
        zones = self.run_all_zones_order()
        if not zones:
            raise HomeAssistantError("No zones to run")
        cycle, soak = self._cycle_soak()
        await self._async_run_steps(
            plan_steps([(zone, self.runtime_for(zone)) for zone in zones], cycle, soak),
            SOURCE_RUN_ALL_ZONES,
        )

    @property
    def blowout_running(self) -> bool:
        """Whether a blowout is in progress."""
        return self._sequence_active and self._sequence_source == SOURCE_BLOWOUT

    def blowout_settings(self) -> tuple[list[int], int, int, int]:
        """Zones, cycles, on seconds and rest seconds from the options."""
        options = self.config_entry.options
        configured = options.get(CONF_BLOWOUT_ZONES)
        zones = list(self.linked_zones)
        if configured:
            zones = [zone for zone in configured if zone in self.linked_zones]
            if skipped := [z for z in configured if z not in self.linked_zones]:
                _LOGGER.warning("Blowout: skipping unknown zones %s", skipped)
        return (
            zones,
            int(options.get(CONF_BLOWOUT_CYCLES, DEFAULT_BLOWOUT_CYCLES)),
            int(options.get(CONF_BLOWOUT_ON_MINUTES, DEFAULT_BLOWOUT_ON_MINUTES)) * 60,
            int(options.get(CONF_BLOWOUT_REST_SECONDS, DEFAULT_BLOWOUT_REST_SECONDS)),
        )

    async def async_blowout(
        self,
        zones: list[int] | None = None,
        cycles: int | None = None,
        on_seconds: int | None = None,
        rest_seconds: int | None = None,
    ) -> None:
        """Blow out the zones: short bursts with rests, one zone after another.

        Anything not given comes from the options.
        """
        default_zones, default_cycles, default_on, default_rest = (
            self.blowout_settings()
        )
        if zones is not None:
            if unknown := [zone for zone in zones if zone not in self.linked_zones]:
                raise HomeAssistantError(f"Unknown zones: {unknown}")
        else:
            zones = default_zones
        if not zones:
            raise HomeAssistantError("No zones to blow out")
        steps = plan_blowout(
            zones,
            default_cycles if cycles is None else cycles,
            # Rain Bird runs zones in whole minutes.
            max(
                60, round((default_on if on_seconds is None else on_seconds) / 60) * 60
            ),
            default_rest if rest_seconds is None else rest_seconds,
        )
        _LOGGER.info("Blowout: %s bursts over zones %s", len(steps), zones)
        await self._async_run_steps(steps, SOURCE_BLOWOUT)

    async def async_cycle_and_soak(self, zone: int, seconds: int) -> None:
        """Run one zone in cycles with soaks between (plain run if no cycle)."""
        cycle, soak = self._cycle_soak()
        if cycle <= 0 or seconds <= cycle:
            await self.async_start_zone(zone, seconds)
            return
        await self._async_run_steps(
            plan_steps([(zone, seconds)], cycle, soak), SOURCE_HOME_ASSISTANT
        )

    async def _async_run_steps(self, steps: list[Step], source: str) -> None:
        self._cancel_sequence()
        self._paused_steps = None
        self._steps = steps
        self._sequence_active = True
        self._sequence_source = source
        await self._async_sequence_next()

    async def _async_sequence_next(self) -> None:
        if not self._steps:
            self._cancel_sequence()
            return
        step = self._steps.pop(0)
        self._current_step = step
        if step.zone is None:
            # Soak: nothing runs until the next cycle.
            self._sequence_zone = None
            end = dt_util.utcnow() + timedelta(seconds=step.seconds)
        else:
            self._sequence_zone = step.zone
            try:
                await self.async_start_zone(step.zone, step.seconds, _sequence=True)
            except HomeAssistantError:
                self._cancel_sequence()
                raise
            end = self._started[step.zone].end

        @callback
        def _next(_now: datetime) -> None:
            self._sequence_unsub = None
            self.config_entry.async_create_background_task(
                self.hass, self._async_sequence_step(), f"{DOMAIN} sequence"
            )

        self._step_end = end
        self._sequence_unsub = async_track_point_in_utc_time(self.hass, _next, end)
        self.async_update_listeners()

    async def _async_sequence_step(self) -> None:
        try:
            await self._async_sequence_next()
        except HomeAssistantError as err:
            _LOGGER.warning("%s stopped: %s", self._sequence_source, err)

    @callback
    def _cancel_sequence(self) -> None:
        was_running = self._sequence_active
        if self._sequence_unsub is not None:
            self._sequence_unsub()
            self._sequence_unsub = None
        self._steps = []
        self._sequence_active = False
        self._sequence_zone = None
        self._current_step = None
        self._step_end = None
        if was_running:
            self.async_update_listeners()

    def _publish_end_times(self) -> None:
        """Update our entities from the switches and runs we started.

        The Rain Bird switches are left alone. They change when Rain Bird's
        own service updates them, and a state listener refreshes us then.
        """
        end_times = self._compute_end_times(self.core.active_zones())
        self._track_runs(end_times)
        self.async_set_updated_data(end_times)
        self._schedule_running_refresh(bool(end_times) or self._sequence_active)

    @callback
    def _cancel_running_refresh(self) -> None:
        if self._running_unsub is not None:
            self._running_unsub()
            self._running_unsub = None

    @callback
    def _schedule_running_refresh(self, needed: bool) -> None:
        """Keep water use and estimates moving while a switch stays on."""
        self._cancel_running_refresh()
        if not needed:
            return

        @callback
        def _due(_now: datetime) -> None:
            self._running_unsub = None
            self.config_entry.async_create_background_task(
                self.hass, self.async_request_refresh(), f"{DOMAIN} running"
            )

        self._running_unsub = async_track_point_in_utc_time(
            self.hass, _due, dt_util.utcnow() + RUNNING_REFRESH
        )
