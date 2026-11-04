"""The public surface of one core Rain Bird config entry.

Rain Bird Extended never opens its own connection and never reads
``runtime_data``, the pyrainbird controller, private locks, or the core
coordinators. Everything here goes through Home Assistant:

- entity registry entries and states that Rain Bird created (zone switches,
  the Rain delay number, the rain sensor, the schedule calendar, and, from
  Home Assistant 2026.10, Run program buttons);
- services Rain Bird or Home Assistant register (``rainbird.start_irrigation``,
  ``rainbird.set_rain_delay`` when it is actually registered, ``switch.turn_off``,
  ``number.set_value``, ``button.press``, ``calendar.get_events``).

Zone switches stay owned by Rain Bird. This module only reads them.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
import re
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .const import (
    RAINBIRD_ATTR_DURATION,
    RAINBIRD_DEFAULT_DURATION_MINUTES,
    RAINBIRD_DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# ``calendar.get_events`` field names from the calendar integration.
_ATTR_START = "start_date_time"
_ATTR_END = "end_date_time"

# Rain Bird's calendar lists zones like this from Home Assistant 2026.11 on
# ("Zone 1: 10 min"). Older releases only have the program's start and end.
_ZONE_LINE = re.compile(r"^Zone\s+(\d+)\s*:\s*(\d+)\s*min\s*$", re.IGNORECASE)
_PROGRAM_SUMMARY = re.compile(r"^(?:PGM|Program)\s+([A-Z])\b", re.IGNORECASE)

SERVICE_START_IRRIGATION = "start_irrigation"
SERVICE_SET_RAIN_DELAY = "set_rain_delay"
SERVICE_SET_VALUE = "set_value"
SERVICE_PRESS = "press"
SERVICE_GET_EVENTS = "get_events"

# One zone run taken from a calendar event: zone, start, end, program index.
type ZoneWindow = tuple[int, datetime, datetime, int | None]
# zone, offset from the program start, duration.
type ZonePattern = tuple[int, timedelta, timedelta]


def program_index(summary: str | None) -> int | None:
    """Program index from a calendar summary such as ``PGM A`` (0 = A)."""
    if not summary:
        return None
    match = _PROGRAM_SUMMARY.match(summary.strip())
    if match is None:
        return None
    return ord(match.group(1).upper()) - ord("A")


def zone_durations(description: str | None) -> list[tuple[int, int]]:
    """``(zone, minutes)`` in run order from a calendar event description.

    Empty when the calendar doesn't list zones (Home Assistant 2026.10 and
    older). The lines are the public description Rain Bird puts on the event,
    not the schedule object behind it.
    """
    if not description:
        return []
    found: list[tuple[int, int]] = []
    for line in description.splitlines():
        if match := _ZONE_LINE.match(line.strip()):
            found.append((int(match.group(1)), int(match.group(2))))
    return found


class RainbirdCore:
    """Read and command one Rain Bird controller through Home Assistant."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Bind to a Rain Bird config entry that is already loaded."""
        self.hass = hass
        self.entry = entry
        self.unique_id = str(entry.unique_id or "")

    @property
    def loaded(self) -> bool:
        """Whether the Rain Bird config entry is set up."""
        return self.entry.state is ConfigEntryState.LOADED

    def _entries(self) -> list[er.RegistryEntry]:
        registry = er.async_get(self.hass)
        return er.async_entries_for_config_entry(registry, self.entry.entry_id)

    def _entity_id(self, domain: str, unique_id: str) -> str | None:
        """Entity id of one Rain Bird registry entry, including disabled ones."""
        for entity in self._entries():
            if entity.domain == domain and entity.unique_id == unique_id:
                return entity.entity_id
        return None

    @callback
    def zone_switches(self) -> dict[int, str]:
        """Zone number to Rain Bird switch entity id.

        Unique ids are ``<controller mac>-<zone>``. Disabled switches are
        included so a zone still exists after someone hides its switch; a
        service call to a disabled switch fails, which the caller reports.
        """
        prefix = f"{self.unique_id}-"
        zones: dict[int, str] = {}
        for entity in self._entries():
            if entity.domain != "switch" or not entity.unique_id.startswith(prefix):
                continue
            suffix = entity.unique_id.removeprefix(prefix)
            if suffix.isdigit():
                zones[int(suffix)] = entity.entity_id
        return zones

    def zone_switch(self, zone: int) -> str | None:
        """The Rain Bird switch for one zone."""
        return self.zone_switches().get(zone)

    @callback
    def zones(self) -> set[int]:
        """Zones Rain Bird has a switch for."""
        return set(self.zone_switches())

    def active_zones(self) -> set[int]:
        """Zones whose Rain Bird switch is on."""
        active: set[int] = set()
        for zone, entity_id in self.zone_switches().items():
            state = self.hass.states.get(entity_id)
            if state is not None and state.state == STATE_ON:
                active.add(zone)
        return active

    def reachable(self) -> bool:
        """Whether Rain Bird is loaded and an enabled switch isn't unavailable.

        A switch with no state is disabled by the user. That doesn't mean the
        controller is offline. Unavailable means Rain Bird can't reach it.
        """
        if not self.loaded:
            return False
        saw_state = False
        for entity_id in self.zone_switches().values():
            state = self.hass.states.get(entity_id)
            if state is None:
                continue
            saw_state = True
            if state.state != STATE_UNAVAILABLE:
                return True
        return not saw_state

    def zone_reachable(self, zone: int) -> bool:
        """Whether this zone's switch isn't reporting unavailable."""
        if not self.reachable() or zone not in self.zones():
            return False
        entity_id = self.zone_switch(zone)
        if entity_id is None:
            return False
        state = self.hass.states.get(entity_id)
        if state is None:
            return True
        return state.state != STATE_UNAVAILABLE

    @property
    def default_duration_minutes(self) -> int:
        """Rain Bird's 'default irrigation time' option, in minutes."""
        try:
            minutes = int(
                self.entry.options.get(
                    RAINBIRD_ATTR_DURATION, RAINBIRD_DEFAULT_DURATION_MINUTES
                )
            )
        except TypeError, ValueError:
            return RAINBIRD_DEFAULT_DURATION_MINUTES
        return max(1, minutes)

    def rain_delay_number(self) -> str | None:
        """The Rain delay number entity id."""
        return self._entity_id("number", f"{self.unique_id}-rain-delay")

    def rain_delay_sensor(self) -> str | None:
        """The older Raindelay sensor, used when the number has no state."""
        return self._entity_id("sensor", f"{self.unique_id}-raindelay")

    def _numeric_state(self, entity_id: str | None) -> int | None:
        if entity_id is None:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return None
        try:
            return int(float(state.state))
        except TypeError, ValueError:
            return None

    def rain_delay_days(self) -> int:
        """Days of rain delay, from the number entity or the sensor."""
        value = self._numeric_state(self.rain_delay_number())
        if value is None:
            value = self._numeric_state(self.rain_delay_sensor())
        return value or 0

    def rain_sensor_on(self) -> bool | None:
        """Rain sensor state, or None when Rain Bird has no reading."""
        entity_id = self._entity_id("binary_sensor", f"{self.unique_id}-rainsensor")
        if entity_id is None:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return None
        if state.state == STATE_ON:
            return True
        if state.state == STATE_OFF:
            return False
        return None

    def program_buttons(self) -> dict[int, str]:
        """Program index to Rain Bird's Run program button, when it has one.

        Those buttons arrived in Home Assistant 2026.10. The sync-time button
        (``<mac>-sync-time``) is not a program.
        """
        prefix = f"{self.unique_id}-program-"
        buttons: dict[int, str] = {}
        for entity in self._entries():
            if entity.domain != "button" or not entity.unique_id.startswith(prefix):
                continue
            suffix = entity.unique_id.removeprefix(prefix)
            if suffix.isdigit():
                buttons[int(suffix)] = entity.entity_id
        return buttons

    def calendar_entity_id(self) -> str | None:
        """Rain Bird's schedule calendar, if this controller has programs."""
        return self._entity_id("calendar", self.unique_id)

    def watched_entity_ids(self) -> set[str]:
        """Rain Bird entities whose state should refresh our coordinator."""
        watched = set(self.zone_switches().values())
        watched.update(self.program_buttons().values())
        for entity_id in (
            self.rain_delay_number(),
            self.rain_delay_sensor(),
            self._entity_id("binary_sensor", f"{self.unique_id}-rainsensor"),
            self.calendar_entity_id(),
        ):
            if entity_id is not None:
                watched.add(entity_id)
        return watched

    async def _async_call(
        self,
        domain: str,
        service: str,
        data: dict[str, Any],
        *,
        return_response: bool = False,
    ) -> Any:
        """Call a service and surface Rain Bird's own errors unchanged."""
        if self.entry.state is not ConfigEntryState.LOADED:
            raise HomeAssistantError("The Rain Bird integration is not loaded")
        if not self.hass.services.has_service(domain, service):
            raise HomeAssistantError(f"The {domain}.{service} action is not available")
        _LOGGER.debug("Calling %s.%s %s", domain, service, data)
        return await self.hass.services.async_call(
            domain,
            service,
            data,
            blocking=True,
            return_response=return_response,
        )

    async def async_start_zone(self, zone: int, minutes: int) -> None:
        """Run a zone via ``rainbird.start_irrigation`` on its switch."""
        entity_id = self.zone_switch(zone)
        if entity_id is None:
            raise HomeAssistantError(f"Rain Bird has no switch for zone {zone}")
        if self.hass.states.get(entity_id) is None:
            raise HomeAssistantError(
                f"{entity_id} is disabled, so the zone can't be started. "
                "Enable the Rain Bird zone switch, or hide it from dashboards "
                "instead of disabling it."
            )
        await self._async_call(
            RAINBIRD_DOMAIN,
            SERVICE_START_IRRIGATION,
            {ATTR_ENTITY_ID: entity_id, RAINBIRD_ATTR_DURATION: minutes},
        )

    async def async_stop(self) -> None:
        """Stop every zone.

        Rain Bird has no separate stop action. Turning a zone switch off stops
        irrigation on the whole controller, which is what that switch does.
        """
        switches = self.zone_switches()
        if not switches:
            raise HomeAssistantError("Rain Bird has no zone switches to turn off")
        active = self.active_zones()
        zone = next(iter(active), min(switches))
        entity_id = switches[zone]
        if self.hass.states.get(entity_id) is None:
            # A disabled switch can't be turned off. Try any enabled one.
            enabled = [
                eid
                for eid in switches.values()
                if self.hass.states.get(eid) is not None
            ]
            if not enabled:
                raise HomeAssistantError(
                    "Rain Bird's zone switches are disabled, so irrigation "
                    "can't be stopped from Home Assistant"
                )
            entity_id = enabled[0]
        await self._async_call("switch", SERVICE_TURN_OFF, {ATTR_ENTITY_ID: entity_id})

    async def async_set_rain_delay(self, days: int) -> None:
        """Set the rain delay.

        ``rainbird.set_rain_delay`` is described by Rain Bird but isn't
        registered in the Home Assistant versions this integration supports.
        Use it when it is, and otherwise set the Rain delay number.
        """
        if self.hass.services.has_service(RAINBIRD_DOMAIN, SERVICE_SET_RAIN_DELAY):
            await self._async_call(
                RAINBIRD_DOMAIN,
                SERVICE_SET_RAIN_DELAY,
                {"config_entry_id": self.entry.entry_id, "duration": days},
            )
            return
        entity_id = self.rain_delay_number()
        if entity_id is None:
            raise HomeAssistantError("Rain Bird has no Rain delay number to set")
        await self._async_call(
            "number", SERVICE_SET_VALUE, {ATTR_ENTITY_ID: entity_id, "value": days}
        )

    async def async_run_program(self, program: int) -> None:
        """Press Rain Bird's Run program button (Home Assistant 2026.10+)."""
        entity_id = self.program_buttons().get(program)
        if entity_id is None:
            letter = chr(ord("A") + program)
            raise HomeAssistantError(
                f"Rain Bird has no Run program {letter} button. "
                "That button was added in Home Assistant 2026.10."
            )
        await self._async_call("button", SERVICE_PRESS, {ATTR_ENTITY_ID: entity_id})

    async def async_calendar_events(
        self, start: datetime, end: datetime
    ) -> list[dict[str, Any]] | None:
        """Events from Rain Bird's schedule calendar.

        None when there is no calendar or the service fails, so the caller
        can keep the events it already had. An empty list is a real answer.
        """
        entity_id = self.calendar_entity_id()
        if entity_id is None or not self.hass.services.has_service(
            "calendar", SERVICE_GET_EVENTS
        ):
            return None
        try:
            response = await self._async_call(
                "calendar",
                SERVICE_GET_EVENTS,
                {
                    ATTR_ENTITY_ID: entity_id,
                    _ATTR_START: start.isoformat(),
                    _ATTR_END: end.isoformat(),
                },
                return_response=True,
            )
        except HomeAssistantError as err:
            _LOGGER.debug("Could not read Rain Bird's schedule calendar: %s", err)
            return None
        return _events_from_response(response)


def _events_from_response(response: Any) -> list[dict[str, Any]]:
    """Flatten a ``calendar.get_events`` response."""
    if not isinstance(response, dict):
        return []
    events: list[dict[str, Any]] = []
    for value in response.values():
        if isinstance(value, dict):
            raw = value.get("events") or []
            events.extend(item for item in raw if isinstance(item, dict))
    return events


def calendar_zone_runs(
    events: list[dict[str, Any]],
) -> tuple[list[ZoneWindow], dict[int, list[ZonePattern]]]:
    """Zone runs and per-program patterns from calendar events.

    Each run is ``(zone, start, end, program index)``. A pattern is
    ``(zone, offset from the program start, duration)`` and is how a program
    started from here is split across zones when the calendar listed them.
    Events without zone lines contribute neither.
    """
    runs: list[ZoneWindow] = []
    patterns: dict[int, list[ZonePattern]] = {}
    for event in events:
        durations = zone_durations(event.get("description"))
        if not durations:
            continue
        start = _as_datetime(event.get("start"))
        if start is None:
            continue
        program = program_index(event.get("summary"))
        offset = timedelta()
        slots: list[ZonePattern] = []
        for zone, minutes in durations:
            if minutes <= 0:
                continue
            duration = timedelta(minutes=minutes)
            runs.append((zone, start + offset, start + offset + duration, program))
            slots.append((zone, offset, duration))
            offset += duration
        if program is not None and program not in patterns and slots:
            patterns[program] = slots
    return runs, patterns


def _as_datetime(value: Any) -> datetime | None:
    """Parse a calendar event start. Date-only values aren't zone runs."""
    parsed: datetime | None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and "T" in value:
        parsed = dt_util.parse_datetime(value)
    else:
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return dt_util.as_local(parsed)
    return parsed
