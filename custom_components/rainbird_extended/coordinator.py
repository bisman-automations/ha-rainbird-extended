"""Coordinator tracking per-zone run times for Rain Bird Extended."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any

from ical.iter import MergedIterable, SortableItem
from ical.timespan import Timespan
from pyrainbird.data import ControllerState, Program, Schedule
from pyrainbird.exceptions import (
    RainbirdApiException,
    RainbirdCodingException,
    RainbirdDeviceBusyException,
    RainbirdDeviceNackError,
)
from pyrainbird.timeline import (
    ProgramEvent,
    ProgramId,
    ProgramTimeline,
    create_recurrence,
)

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CYCLE_MINUTES,
    CONF_RUN_ALL_ZONES,
    CONF_SOAK_MINUTES,
    CONFIRM_DELAY,
    CONTROLLER_STATE_IDLE_REFRESH,
    DEFAULT_SOAK_MINUTES,
    DOMAIN,
    EARLY_STOP_MARGIN,
    END_TIME_TOLERANCE,
    LCR_BUDGET,
    RAINBIRD_ATTR_DURATION,
    RAINBIRD_DEFAULT_DURATION_MINUTES,
    SCHEDULE_TOLERANCE,
    START_GRACE_PERIOD,
    TIMEOUT_SECONDS,
)
from .sequence import Step, plan_steps

if TYPE_CHECKING:
    from homeassistant.components.rainbird.coordinator import (
        RainbirdScheduleUpdateCoordinator,
        RainbirdUpdateCoordinator,
    )

_LOGGER = logging.getLogger(__name__)


SOURCE_HOME_ASSISTANT = "home_assistant"
SOURCE_RUN_ALL_ZONES = "run_all_zones"
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

# zone number -> when the current run is expected to end (None if unknown)
type ZoneEndTimes = dict[int, datetime | None]


class RainbirdExtendedCoordinator(DataUpdateCoordinator[ZoneEndTimes]):
    """Compute when each running zone will finish, and track runs.

    This coordinator does not poll on its own schedule. It refreshes whenever the
    core Rain Bird coordinator refreshes (about once a minute) and after a zone is
    started from here. It talks to the controller while a zone is running, and
    every 30 minutes to keep the seasonal adjustment current.
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
        rainbird_data = rainbird_entry.runtime_data
        # Kept to notice when the core entry reloads and replaces it.
        self.rainbird_data = rainbird_data
        self.rainbird: RainbirdUpdateCoordinator = rainbird_data.coordinator
        self.schedule: RainbirdScheduleUpdateCoordinator | None = getattr(
            rainbird_data, "schedule_coordinator", None
        )
        self.controller = rainbird_data.controller
        model = getattr(rainbird_data, "model_info", None)
        # Works with pyrainbird 6.5 (Home Assistant 2026.9) and newer.
        info = model.model_info if model else None
        self.max_programs: int = info.max_programs if info else 0
        self.supports_water_budget: bool = bool(info and info.supports_water_budget)
        # Program-based controllers have a seasonal adjust per program; the
        # others (LCR: ESP-RZXe, ST8) have one for the whole controller.
        self.program_based: bool = self.max_programs > 0
        self.max_seasonal_adjust: int = getattr(
            getattr(info, "limits", None), "max_seasonal_adjust", 200
        )
        # Setting it needs pyrainbird 6.6 or newer.
        self.can_set_water_budget: bool = hasattr(self.controller, "set_water_budget")
        # Seasonal adjust (%) by program index, or LCR_BUDGET for LCR controllers.
        self.water_budgets: dict[int, int] = {}
        self._water_budgets_at: datetime | None = None
        # (program index, local start) of a program started from here.
        self._manual_program: tuple[int, datetime] | None = None
        # Rain Bird controllers handle one request at a time. Share the core
        # integration's lock when it has one so we never poll concurrently.
        self._device_lock: asyncio.Lock = (
            getattr(self.rainbird, "_device_lock", None) or asyncio.Lock()
        )
        # None = not probed yet, False = controller NACKs the request.
        self._supports_controller_state: bool | None = None
        self.controller_state: ControllerState | None = None
        self._controller_state_at: datetime | None = None
        # Runs started from Home Assistant, by zone.
        self._started: dict[int, _LocalRun] = {}
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
        self._sequence_unsub: CALLBACK_TYPE | None = None
        # zone -> callbacks for run started/finished.
        self._run_listeners: dict[int, list[RunListener]] = {}

    @property
    def rainbird_unique_id(self) -> str | None:
        """Unique id of the core Rain Bird config entry (formatted MAC)."""
        return self.rainbird.unique_id

    @property
    def zones(self) -> set[int]:
        """All zones known to the controller."""
        return set(self.rainbird.data.zones) if self.rainbird.data else set()

    @property
    def supports_controller_state(self) -> bool | None:
        """Whether the controller reports remaining run time (None = unknown)."""
        return self._supports_controller_state

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
        """Zones the controller reports as running."""
        return set(self.rainbird.data.active_zones) if self.rainbird.data else set()

    @property
    def default_runtime(self) -> int:
        """Default runtime in seconds, from the core integration's options."""
        minutes = self.rainbird_entry.options.get(
            RAINBIRD_ATTR_DURATION, RAINBIRD_DEFAULT_DURATION_MINUTES
        )
        return int(minutes) * 60

    def runtime_for(self, zone: int) -> int:
        """Return the configured runtime for a zone in seconds."""
        return self.runtimes.get(zone, self.default_runtime)

    @callback
    def async_start(self) -> None:
        """Follow the core coordinators so our data refreshes with them."""
        self.config_entry.async_on_unload(
            self.rainbird.async_add_listener(self._handle_rainbird_update)
        )
        if self.schedule is not None and self.max_programs:
            # Listening also makes the core coordinator poll the schedule.
            self.config_entry.async_on_unload(
                self.schedule.async_add_listener(self.async_update_listeners)
            )
            if self.schedule.data is None:
                self.config_entry.async_create_background_task(
                    self.hass,
                    self.schedule.async_request_refresh(),
                    f"{DOMAIN} schedule",
                )
        self.config_entry.async_on_unload(self._cancel_sequence)

    @callback
    def _handle_rainbird_update(self) -> None:
        """Refresh our end times whenever the core integration refreshes."""
        self.config_entry.async_create_background_task(
            self.hass, self.async_request_refresh(), f"{DOMAIN} refresh"
        )

    async def _async_update_data(self) -> ZoneEndTimes:
        """Work out the expected end time for each running zone."""
        active = self.active_zones
        now = dt_util.utcnow()
        if self.supports_water_budget and (
            self._water_budgets_at is None
            or now - self._water_budgets_at > CONTROLLER_STATE_IDLE_REFRESH
        ):
            await self._async_fetch_water_budgets()
        controller_state: ControllerState | None = None
        if self._supports_controller_state is not False:
            if active:
                controller_state = await self._async_fetch_controller_state()
            elif not self.supports_water_budget and (
                self._controller_state_at is None
                or now - self._controller_state_at > CONTROLLER_STATE_IDLE_REFRESH
            ):
                # Only for the seasonal adjustment; never fail the update.
                try:
                    await self._async_fetch_controller_state()
                except UpdateFailed as err:
                    _LOGGER.debug("Could not read controller state: %s", err)
        end_times = self._compute_end_times(active, controller_state)
        self._track_runs(end_times)
        return end_times

    @property
    def water_budget_keys(self) -> list[int]:
        """Program indexes with their own seasonal adjust (LCR: one for all)."""
        if self.program_based and self.max_programs:
            return list(range(self.max_programs))
        return [LCR_BUDGET]

    async def _async_fetch_water_budgets(self) -> None:
        """Read each program's seasonal adjust; never fails the update."""
        self._water_budgets_at = dt_util.utcnow()
        for key in self.water_budget_keys:
            try:
                async with self._device_lock, asyncio.timeout(TIMEOUT_SECONDS):
                    budget = await self.controller.water_budget(key)
            except (RainbirdDeviceNackError, RainbirdCodingException) as err:
                _LOGGER.info("Controller does not report seasonal adjust: %s", err)
                self.supports_water_budget = False
                self.water_budgets.clear()
                return
            except (RainbirdApiException, TimeoutError, KeyError, ValueError) as err:
                _LOGGER.debug("Could not read seasonal adjust: %s", err)
                self._water_budgets_at = None
                return
            self.water_budgets[key] = int(budget.adjust)

    async def async_set_water_budget(self, key: int, percent: int) -> None:
        """Set a program's seasonal adjust (LCR: the controller's)."""
        await self._async_command(self.controller.set_water_budget, key, percent)
        self.water_budgets[key] = percent
        self.async_update_listeners()

    async def _async_fetch_controller_state(self) -> ControllerState | None:
        """Ask the controller how long the running zone has left."""
        try:
            async with self._device_lock, asyncio.timeout(TIMEOUT_SECONDS):
                state = await self.controller.get_combined_controller_state()
        except RainbirdDeviceBusyException:
            _LOGGER.debug("Controller busy; keeping previous end times")
            return None
        except (RainbirdDeviceNackError, RainbirdCodingException, KeyError, ValueError):
            # Older controllers don't implement this command; fall back to the
            # end times we track for runs started from Home Assistant.
            _LOGGER.info(
                "Controller does not report remaining run time; time remaining "
                "is only known for runs started from Home Assistant"
            )
            self._supports_controller_state = False
            return None
        except TimeoutError as err:
            raise UpdateFailed("Timed out talking to Rain Bird controller") from err
        except RainbirdApiException as err:
            raise UpdateFailed(f"Rain Bird controller error: {err}") from err
        self._supports_controller_state = True
        self.controller_state = state
        self._controller_state_at = dt_util.utcnow()
        return state

    def _compute_end_times(
        self, active: set[int], controller_state: ControllerState | None
    ) -> ZoneEndTimes:
        """Combine controller-reported and locally tracked end times."""
        now = dt_util.utcnow()
        previous = self.data or {}

        # Forget runs that have stopped. A freshly started run gets a grace
        # period because the controller is slow to report it as active, but
        # once it has been seen running, going idle means it is finished.
        for zone, run in list(self._started.items()):
            if zone in active:
                # Our own optimistic update marks the zone active immediately,
                # so only trust "active" once the controller has had time to
                # report it.
                if now - run.started_at > CONFIRM_DELAY:
                    run.seen_active = True
            elif run.seen_active or now - run.started_at > START_GRACE_PERIOD:
                del self._started[zone]
                if zone == self._sequence_zone and now < run.end - EARLY_STOP_MARGIN:
                    _LOGGER.debug("Zone %s stopped early; ending run all zones", zone)
                    self._cancel_sequence()

        reported: dict[int, datetime] = {}
        if controller_state is not None and controller_state.remaining_runtime > 0:
            zone = controller_state.active_station
            if zone not in active and len(active) == 1:
                zone = next(iter(active))
            if zone in active:
                reported[zone] = now + timedelta(
                    seconds=controller_state.remaining_runtime
                )

        return {
            zone: self._end_time(
                zone,
                reported.get(zone),
                zone in active,
                controller_state is None,
                previous.get(zone),
            )
            for zone in active | set(self._started)
        }

    def _end_time(
        self,
        zone: int,
        reported: datetime | None,
        active: bool,
        no_controller_state: bool,
        previous: datetime | None,
    ) -> datetime | None:
        """Pick the best known end time for one zone."""
        end = reported
        if end is None and zone in self._started:
            end = self._started[zone].end
        if end is None and active:
            # The controller didn't say; work it out from the schedule.
            end = self.scheduled_end(zone)
        if end is None and no_controller_state:
            # Controller busy or unsupported this round: keep what we had.
            end = previous
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
                    # between, it ran until then rather than until now.
                    prev_end = self._prev_end_times.get(zone)
                    if prev_end is not None and prev_end < now:
                        until = max(prev_end, self._last_tick)
                    if (run := self.last_runs.get(zone)) and run.end is None:
                        run.end = until
                        self._fire_run_event(zone, EVENT_FINISHED, run)
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
        if self._manual_program is not None and (schedule := self._schedule):
            index, _ = self._manual_program
            program = next((p for p in schedule.programs if p.program == index), None)
            if program is not None and self._program_slot(program, zone):
                return SOURCE_PROGRAM
        if self.scheduled_end(zone) is not None:
            return SOURCE_SCHEDULE
        return SOURCE_OTHER

    def _fire_run_event(self, zone: int, event: str, run: ZoneRun) -> None:
        for listener in list(self._run_listeners.get(zone, [])):
            listener(event, run)

    @property
    def _schedule(self) -> Schedule | None:
        if self.schedule is None or not isinstance(self.schedule.data, Schedule):
            return None
        return self.schedule.data

    def _budget_scale(self, program: int) -> float:
        """How seasonal adjust stretches a program's runtimes (1.0 = as set)."""
        percent = self.water_budgets.get(program, self.water_budgets.get(LCR_BUDGET))
        return (percent or 100) / 100

    def _program_slot(
        self, program: Program, zone: int
    ) -> tuple[timedelta, timedelta] | None:
        """Offset from the program start and duration of a zone's run in it.

        Zones in a program run one after another, in zone order, each for its
        runtime scaled by the program's seasonal adjust.
        """
        scale = self._budget_scale(program.program)
        offset = timedelta()
        for zone_duration in program.durations:
            duration = zone_duration.duration * scale
            if zone_duration.zone == zone:
                return (offset, duration) if duration else None
            offset += duration
        return None

    def _zone_timeline(self, zone: int, now: datetime) -> ProgramTimeline | None:
        """Every scheduled run of a zone, from the controller's schedule."""
        if (schedule := self._schedule) is None:
            return None
        iters: list[Iterable[SortableItem[Timespan, ProgramEvent]]] = []
        for program in schedule.programs:
            if (slot := self._program_slot(program, zone)) is None:
                continue
            offset, duration = slot
            for start in program.starts:
                dtstart = (
                    now.replace(
                        hour=start.hour, minute=start.minute, second=0, microsecond=0
                    )
                    + offset
                )
                iters.append(
                    create_recurrence(
                        ProgramId(program.program, zone),
                        program.frequency,
                        dtstart,
                        duration,
                        program.synchro or 0,
                        program.days_of_week,
                        program.period or 0,
                        delay_days=schedule.delay_days,
                    )
                )
        for zone_schedule in schedule.zone_schedules.values():
            if zone_schedule.zone == zone:
                iters.extend(zone_schedule.timeline_iters(now.tzinfo))
        return ProgramTimeline(MergedIterable(iters)) if iters else None

    def next_run(self, zone: int) -> datetime | None:
        """When the controller's schedule next runs this zone."""
        now = dt_util.now()
        if (timeline := self._zone_timeline(zone, now)) is None:
            return None
        event = next(timeline.start_after(now), None)
        return dt_util.as_utc(event.start) if event else None

    def scheduled_end(self, zone: int) -> datetime | None:
        """When a running zone should finish, worked out from the schedule.

        Used when the controller doesn't report the remaining run time: for a
        program started from here, and for scheduled runs.
        """
        now = dt_util.now()
        if self._manual_program is not None and (schedule := self._schedule):
            index, started = self._manual_program
            program = next((p for p in schedule.programs if p.program == index), None)
            if program is not None and (slot := self._program_slot(program, zone)):
                offset, duration = slot
                if started + offset - SCHEDULE_TOLERANCE <= now:
                    end = started + offset + duration
                    if now < end + SCHEDULE_TOLERANCE:
                        return dt_util.as_utc(end)
        if (timeline := self._zone_timeline(zone, now)) is None:
            return None
        event = next(timeline.active_after(now - SCHEDULE_TOLERANCE), None)
        if event is not None and event.start - SCHEDULE_TOLERANCE <= now:
            return dt_util.as_utc(event.end)
        return None

    async def async_start_zone(
        self, zone: int, seconds: int | None = None, *, _sequence: bool = False
    ) -> None:
        """Run a zone for its configured runtime (or the given seconds)."""
        if not _sequence:
            self._cancel_sequence()
        self._manual_program = None
        seconds = self.runtime_for(zone) if seconds is None else seconds
        minutes = max(1, round(seconds / 60))
        await self._async_command(self.controller.irrigate_zone, zone, minutes)
        now = dt_util.utcnow()
        self._started = {zone: _LocalRun(now + timedelta(minutes=minutes), now)}
        self._optimistic_active({zone})

    async def async_stop(self) -> None:
        """Stop all irrigation (Rain Bird can't stop a single zone)."""
        self._cancel_sequence()
        self._manual_program = None
        await self._async_command(self.controller.stop_irrigation)
        self._started.clear()
        self._optimistic_active(set())

    async def async_run_program(self, program: int) -> None:
        """Start one of the controller's programs (0 = A)."""
        self._cancel_sequence()
        await self._async_command(self.controller.set_program, program)
        self._started.clear()
        self._manual_program = (program, dt_util.now())
        self.config_entry.async_create_background_task(
            self.hass, self.rainbird.async_request_refresh(), f"{DOMAIN} confirm"
        )

    async def async_set_rain_delay(self, days: int) -> None:
        """Set the controller's rain delay and refresh the core entities."""
        await self._async_command(self.controller.set_rain_delay, days)
        self.config_entry.async_create_background_task(
            self.hass, self.rainbird.async_request_refresh(), f"{DOMAIN} confirm"
        )

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
        self._steps = steps
        self._sequence_active = True
        self._sequence_source = source
        await self._async_sequence_next()

    async def _async_sequence_next(self) -> None:
        if not self._steps:
            self._cancel_sequence()
            return
        step = self._steps.pop(0)
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

        self._sequence_unsub = async_track_point_in_utc_time(self.hass, _next, end)
        self.async_update_listeners()

    async def _async_sequence_step(self) -> None:
        try:
            await self._async_sequence_next()
        except HomeAssistantError as err:
            _LOGGER.warning("Run all zones stopped: %s", err)

    @callback
    def _cancel_sequence(self) -> None:
        was_running = self._sequence_active
        if self._sequence_unsub is not None:
            self._sequence_unsub()
            self._sequence_unsub = None
        self._steps = []
        self._sequence_active = False
        self._sequence_zone = None
        if was_running:
            self.async_update_listeners()

    async def _async_command(self, func: Callable[..., Any], *args: Any) -> None:
        if self.rainbird_entry.state is not ConfigEntryState.LOADED:
            raise HomeAssistantError("The Rain Bird integration is not loaded")
        try:
            async with self._device_lock, asyncio.timeout(TIMEOUT_SECONDS):
                await func(*args)
        except RainbirdDeviceBusyException as err:
            raise HomeAssistantError(
                "Rain Bird device is busy; wait and try again"
            ) from err
        except (RainbirdApiException, TimeoutError) as err:
            raise HomeAssistantError("Rain Bird device failure") from err

    def _optimistic_active(self, active: set[int]) -> None:
        """Reflect a command right away; the controller lags a few seconds.

        This also updates the core integration's switches, then asks the core
        coordinator for a (debounced) refresh to confirm the real state.
        """
        if self.rainbird.data is not None:
            self.rainbird.data.active_zones.clear()
            self.rainbird.data.active_zones.update(active)
        end_times = self._compute_end_times(active, None) if active else {}
        self._track_runs(end_times)
        self.async_set_updated_data(end_times)
        self.rainbird.async_update_listeners()
        self.config_entry.async_create_background_task(
            self.hass, self.rainbird.async_request_refresh(), f"{DOMAIN} confirm"
        )
