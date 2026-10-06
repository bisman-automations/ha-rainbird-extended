"""Coordinator tracking per-zone run times for Rain Bird Extended."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any

from pyrainbird.data import ControllerState
from pyrainbird.exceptions import (
    RainbirdApiException,
    RainbirdCodingException,
    RainbirdDeviceBusyException,
    RainbirdDeviceNackError,
)

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONFIRM_DELAY,
    DOMAIN,
    END_TIME_TOLERANCE,
    RAINBIRD_ATTR_DURATION,
    RAINBIRD_DEFAULT_DURATION_MINUTES,
    START_GRACE_PERIOD,
    TIMEOUT_SECONDS,
)

if TYPE_CHECKING:
    from homeassistant.components.rainbird.coordinator import RainbirdUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass
class _LocalRun:
    """A run started from Home Assistant."""

    end: datetime
    started_at: datetime
    seen_active: bool = False


# zone number -> when the current run is expected to end (None if unknown)
type ZoneEndTimes = dict[int, datetime | None]


class RainbirdExtendedCoordinator(DataUpdateCoordinator[ZoneEndTimes]):
    """Compute when each running zone will finish.

    This coordinator does not poll on its own schedule. It refreshes whenever the
    core Rain Bird coordinator refreshes (about once a minute) and after a zone is
    started from here, and only talks to the controller while a zone is running.
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
        self.controller = rainbird_data.controller
        # Rain Bird controllers handle one request at a time. Share the core
        # integration's lock when it has one so we never poll concurrently.
        self._device_lock: asyncio.Lock = (
            getattr(self.rainbird, "_device_lock", None) or asyncio.Lock()
        )
        # None = not probed yet, False = controller NACKs the request.
        self._supports_controller_state: bool | None = None
        # Runs started from Home Assistant, by zone.
        self._started: dict[int, _LocalRun] = {}
        # zone -> runtime in seconds, kept in sync by the number entities.
        self.runtimes: dict[int, int] = {}

    @property
    def rainbird_unique_id(self) -> str | None:
        """Unique id of the core Rain Bird config entry (formatted MAC)."""
        return self.rainbird.unique_id

    @property
    def zones(self) -> set[int]:
        """All zones known to the controller."""
        return set(self.rainbird.data.zones) if self.rainbird.data else set()

    def linkable_zones(self) -> list[int]:
        """Zones whose core Rain Bird device exists, in order."""
        from .entity import find_zone_device  # noqa: PLC0415

        zones = []
        for zone in sorted(self.zones):
            if find_zone_device(
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
        """Follow the core coordinator so our data refreshes with it."""
        self.config_entry.async_on_unload(
            self.rainbird.async_add_listener(self._handle_rainbird_update)
        )

    @callback
    def _handle_rainbird_update(self) -> None:
        """Refresh our end times whenever the core integration refreshes."""
        self.config_entry.async_create_background_task(
            self.hass, self.async_request_refresh(), f"{DOMAIN} refresh"
        )

    async def _async_update_data(self) -> ZoneEndTimes:
        """Work out the expected end time for each running zone."""
        active = self.active_zones
        controller_state: ControllerState | None = None
        if active and self._supports_controller_state is not False:
            controller_state = await self._async_fetch_controller_state()
        return self._compute_end_times(active, controller_state)

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

        reported: dict[int, datetime] = {}
        if controller_state is not None and controller_state.remaining_runtime > 0:
            zone = controller_state.active_station
            if zone not in active and len(active) == 1:
                zone = next(iter(active))
            if zone in active:
                reported[zone] = now + timedelta(
                    seconds=controller_state.remaining_runtime
                )

        result: ZoneEndTimes = {}
        for zone in active | set(self._started):
            end = reported.get(zone)
            if end is None and zone in self._started:
                end = self._started[zone].end
            if end is None and controller_state is None:
                # Controller busy or unsupported this round: keep what we had.
                end = previous.get(zone)
            if (
                end is not None
                and (prev := previous.get(zone)) is not None
                and abs(end - prev) < END_TIME_TOLERANCE
            ):
                end = prev
            result[zone] = end
        return result

    async def async_start_zone(self, zone: int, seconds: int | None = None) -> None:
        """Run a zone for its configured runtime (or the given seconds)."""
        seconds = self.runtime_for(zone) if seconds is None else seconds
        minutes = max(1, round(seconds / 60))
        await self._async_command(self.controller.irrigate_zone, zone, minutes)
        now = dt_util.utcnow()
        self._started = {zone: _LocalRun(now + timedelta(minutes=minutes), now)}
        self._optimistic_active({zone})

    async def async_stop(self) -> None:
        """Stop all irrigation (Rain Bird can't stop a single zone)."""
        await self._async_command(self.controller.stop_irrigation)
        self._started.clear()
        self._optimistic_active(set())

    async def _async_command(self, func: Any, *args: Any) -> None:
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
        self.async_set_updated_data(
            self._compute_end_times(active, None) if active else {}
        )
        self.rainbird.async_update_listeners()
        self.config_entry.async_create_background_task(
            self.hass, self.rainbird.async_request_refresh(), f"{DOMAIN} confirm"
        )
