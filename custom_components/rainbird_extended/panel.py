"""Admin-only sidebar panel for configuring Rain Bird Extended."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util

from .config_flow import MAX_PROGRAMS
from .const import (
    CONF_POWER_ENTITY,
    CONF_RAINBIRD_ENTRY_ID,
    CONF_STOP_ACTION,
    CONF_WEATHER_ENTITY,
    DEFAULT_STOP_ACTION,
    DOMAIN,
)
from .programs import ConfiguredProgram, parse_programs, program_letter
from .yard import BACKGROUNDS, async_get_yard_store

_LOGGER = logging.getLogger(__name__)

PANEL_URL_PATH = f"{DOMAIN}-dashboard"
STATIC_URL = f"/{DOMAIN}_static"
PANEL_COMPONENT = "rainbird-extended-panel"
FRONTEND_DIR = Path(__file__).parent / "frontend"
_PANEL_REGISTERED = f"{DOMAIN}_panel_registered"
_STATIC_REGISTERED = f"{DOMAIN}_panel_static_registered"
_PANEL_VERSION = f"{DOMAIN}_panel_version"
_API_REGISTERED = f"{DOMAIN}_panel_api_registered"
_PANEL_LOCK = f"{DOMAIN}_panel_lock"


def async_setup_panel_api(hass: HomeAssistant) -> None:
    """Register the administrator-only dashboard data command once."""
    if hass.data.get(_API_REGISTERED):
        return
    websocket_api.async_register_command(hass, websocket_get_panel_config)
    websocket_api.async_register_command(hass, websocket_set_rain_delay)
    websocket_api.async_register_command(hass, websocket_save_yard)
    websocket_api.async_register_command(hass, websocket_apple_key)
    websocket_api.async_register_command(hass, websocket_apple_token)
    websocket_api.async_register_command(hass, websocket_zone_hidden)
    hass.data[_API_REGISTERED] = True


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/panel/config"})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_get_panel_config(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return safe controller and per-zone configuration summaries."""
    result = _panel_config(hass)
    await _async_add_forecasts(hass, result["controllers"])
    yard = await async_get_yard_store(hass)
    for controller in result["controllers"]:
        controller["yard"] = yard.get(controller["entry_id"], hass.config.country)
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/panel/yard",
        vol.Required("entry_id"): str,
        vol.Optional("background"): vol.In(BACKGROUNDS),
        vol.Optional("esri_key"): str,
        vol.Optional("zone"): vol.All(vol.Coerce(int), vol.Range(min=1, max=64)),
        vol.Optional("shape"): vol.Any(None, list),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_save_yard(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Save the map background, the ArcGIS key, or one zone's outline.

    A zone with ``shape: null`` has its outline removed.
    """
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    if entry is None or entry.domain != DOMAIN:
        connection.send_error(
            msg["id"], "not_found", "Rain Bird Extended entry not found"
        )
        return
    yard = await async_get_yard_store(hass)
    try:
        await yard.async_update(
            entry.entry_id,
            background=msg.get("background"),
            esri_key=msg.get("esri_key"),
            zone=msg.get("zone"),
            shape=msg.get("shape"),
            remove_zone="zone" in msg and "shape" in msg and msg["shape"] is None,
        )
    except ValueError as err:
        connection.send_error(msg["id"], "invalid", str(err))
        return
    connection.send_result(msg["id"], yard.get(entry.entry_id, hass.config.country))


async def _async_add_forecasts(
    hass: HomeAssistant, controllers: list[dict[str, Any]]
) -> None:
    """Add up to ten daily forecasts from each controller's configured weather."""
    for controller in controllers:
        controller["forecast"] = []
        controller["forecast_error"] = None
        weather_entity = controller.get("weather_entity")
        if not weather_entity:
            continue
        if hass.states.get(weather_entity) is None:
            controller["forecast_error"] = f"{weather_entity} is unavailable."
            continue
        try:
            response = await hass.services.async_call(
                "weather",
                "get_forecasts",
                {"type": "daily"},
                target={"entity_id": weather_entity},
                blocking=True,
                return_response=True,
            )
        except HomeAssistantError as err:
            controller["forecast_error"] = str(err)
            continue

        result = (response or {}).get(weather_entity)
        forecasts = result.get("forecast") if isinstance(result, dict) else None
        if not isinstance(forecasts, list):
            controller["forecast_error"] = "The weather entity returned no forecast."
            continue

        weather_state = hass.states.get(weather_entity)
        controller["forecast_unit"] = (
            weather_state.attributes.get("temperature_unit", "")
            if weather_state
            else ""
        )
        controller["forecast"] = [
            summary
            for forecast in forecasts
            if isinstance(forecast, dict)
            and (summary := _forecast_summary(forecast)) is not None
        ][:10]
        if not controller["forecast"]:
            controller["forecast_error"] = "The weather entity returned no forecast."


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/panel/yard/apple_key",
        vol.Optional("team_id"): str,
        vol.Optional("key_id"): str,
        vol.Optional("private_key"): str,
        vol.Optional("clear"): bool,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_apple_key(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Keep or forget the Apple MapKit key used for the Apple Maps background."""
    yard = await async_get_yard_store(hass)
    try:
        if msg.get("clear"):
            await yard.async_clear_apple()
        else:
            await yard.async_set_apple(
                msg.get("team_id", ""),
                msg.get("key_id", ""),
                msg.get("private_key", ""),
            )
    except ValueError as err:
        connection.send_error(msg["id"], "invalid", str(err))
        return
    connection.send_result(msg["id"], {"apple": yard.get("")["apple"]})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/panel/yard/apple_token",
        vol.Optional("origin"): vol.All(str, vol.Match(r"^https?://[^/\s]+$")),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_apple_token(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """A 30-minute MapKit JS token for the page asking, signed in Home Assistant."""
    yard = await async_get_yard_store(hass)
    try:
        token = yard.apple_token(msg.get("origin"))
    except ValueError as err:
        connection.send_error(msg["id"], "not_set_up", str(err))
        return
    connection.send_result(msg["id"], {"token": token})


def _hidden_zones(
    device_registry: dr.DeviceRegistry, entry_id: str, uid: str | None
) -> list[dict[str, Any]]:
    """Zones whose Rain Bird Extended device is turned off, by name."""
    if not uid:
        return []
    hidden = []
    for device in dr.async_entries_for_config_entry(device_registry, entry_id):
        if device.disabled_by is None:
            continue
        for domain, value in device.identifiers:
            zone = value.removeprefix(f"{uid}-")
            if domain == DOMAIN and zone != value and zone.isdigit():
                hidden.append(
                    {
                        "zone": int(zone),
                        "name": device.name_by_user or device.name or f"Zone {zone}",
                    }
                )
    return sorted(hidden, key=lambda item: item["zone"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/panel/zone_hidden",
        vol.Required("entry_id"): str,
        vol.Required("zone"): vol.All(vol.Coerce(int), vol.Range(min=1, max=64)),
        vol.Required("hidden"): bool,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_zone_hidden(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Hide a zone: turn off its Rain Bird Extended device, or turn it back on.

    Turning the device off turns off its entities, so the zone leaves the
    panel. Rain Bird's own zone switch and device are not touched.
    """
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    rainbird_entry = (
        hass.config_entries.async_get_entry(entry.data[CONF_RAINBIRD_ENTRY_ID])
        if entry is not None and entry.domain == DOMAIN
        else None
    )
    registry = dr.async_get(hass)
    device = (
        registry.async_get_device_by_identifier(
            (DOMAIN, f"{rainbird_entry.unique_id}-{msg['zone']}"), entry.entry_id
        )
        if entry is not None and rainbird_entry and rainbird_entry.unique_id
        else None
    )
    if entry is None or device is None:
        connection.send_error(msg["id"], "not_found", "That zone wasn't found")
        return
    if msg["hidden"]:
        registry.async_update_device(device.id, disabled_by=dr.DeviceEntryDisabler.USER)
    elif device.disabled_by is not None:
        registry.async_update_device(device.id, disabled_by=None)
        # Bring the zone's entities back now rather than after Home
        # Assistant's 30-second wait.
        hass.config_entries.async_schedule_reload(entry.entry_id)
    connection.send_result(msg["id"], {"zone": msg["zone"], "hidden": msg["hidden"]})


def _forecast_summary(forecast: dict[str, Any]) -> dict[str, Any] | None:
    """Keep forecast data small and JSON-safe for the dashboard."""
    forecast_datetime = dt_util.parse_datetime(str(forecast.get("datetime", "")))
    if forecast_datetime is None:
        return None
    return {
        "date": dt_util.as_local(forecast_datetime).date().isoformat(),
        "condition": forecast.get("condition"),
        "precipitation_probability": forecast.get("precipitation_probability"),
        "temperature": forecast.get("temperature"),
        "templow": forecast.get("templow"),
    }


def _zone_names(
    hass: HomeAssistant,
    registry: dr.DeviceRegistry,
    rainbird_entities: list[Any],
    extended_entities: list[Any],
) -> dict[int, str]:
    """Get each zone's linked entity name, falling back to its device name."""
    names: dict[int, str] = {}
    for entity in extended_entities:
        if entity.disabled_by is not None or not entity.device_id:
            continue
        if entity.domain == "valve":
            parts = entity.unique_id.rsplit("-", 2)
            if len(parts) != 3 or not parts[1].isdigit() or parts[2] != "valve":
                continue
        elif entity.domain == "number":
            parts = entity.unique_id.rsplit("-", 2)
            if len(parts) != 3 or not parts[1].isdigit():
                continue
        else:
            continue
        zone = int(parts[1])
        device = registry.async_get(entity.device_id)
        if device is not None:
            names.setdefault(zone, device.name_by_user or device.name or f"Zone {zone}")

    for entity in rainbird_entities:
        if entity.domain != "switch" or entity.disabled_by is not None:
            continue
        zone_id = entity.unique_id.rsplit("-", 1)[-1]
        state = hass.states.get(entity.entity_id)
        if zone_id.isdigit() and state is not None:
            friendly_name = state.attributes.get("friendly_name")
            if isinstance(friendly_name, str) and friendly_name:
                names[int(zone_id)] = friendly_name
    return names


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/panel/rain_delay",
        vol.Required("entry_id"): str,
        vol.Required("days"): vol.All(vol.Coerce(int), vol.Range(min=0, max=14)),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_set_rain_delay(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Set the controller rain delay and recalculate program runs."""
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    if entry is None or entry.domain != DOMAIN:
        connection.send_error(
            msg["id"], "not_found", "Rain Bird Extended entry not found"
        )
        return
    if entry.state is not ConfigEntryState.LOADED:
        connection.send_error(
            msg["id"], "not_loaded", "Rain Bird Extended is not loaded"
        )
        return
    try:
        await entry.runtime_data.async_set_rain_delay(
            msg["days"], wait_for_refresh=True
        )
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "failed", str(err))
        return
    connection.send_result(msg["id"], {"days": msg["days"]})


def _program_summaries(
    options: Mapping[str, Any],
    count: int,
    coordinator: Any,
    now: datetime,
) -> list[dict[str, Any]]:
    """Describe configured programs and their next scheduled run date."""
    configured = {
        program.program: program for program in parse_programs(options, count)
    }
    summaries = []
    delay_days = getattr(coordinator, "rain_delay_days", 0) if coordinator else 0
    # A controller that's turned off doesn't run its programs.
    turned_off = bool(getattr(coordinator, "turned_off", False))
    for index in range(count):
        program = configured.get(index)
        next_run = (
            _next_program_run(program, now, delay_days)
            if program and not turned_off
            else None
        )
        summaries.append(
            {
                "program": index,
                "letter": program_letter(index),
                "name": program.name if program else "",
                "configured": program is not None,
                "next_run": next_run.date().isoformat() if next_run else None,
            }
        )
    return summaries


def _next_program_run(
    program: ConfiguredProgram, now: datetime, delay_days: int
) -> datetime | None:
    """Find the next occurrence across a configured program's start times."""
    upcoming = []
    recurrence_now = now
    if program.start_date and program.start_date > now.date():
        recurrence_now = datetime.combine(
            program.start_date, datetime.min.time(), tzinfo=now.tzinfo
        )
    for start in program.starts:
        recurrence = program.recurrence(
            start,
            recurrence_now,
            timedelta(),
            timedelta(minutes=1),
            delay_days=delay_days,
        )
        next_run = next(
            (item.key.start for item in recurrence if item.key.start >= now), None
        )
        if next_run is not None:
            upcoming.append(next_run)
    return min(upcoming) if upcoming else None


def _panel_config(hass: HomeAssistant) -> dict[str, list[dict[str, Any]]]:
    """Build a dashboard model without exposing Rain Bird credentials."""
    registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    controllers: list[dict[str, Any]] = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        rainbird_entry = hass.config_entries.async_get_entry(
            entry.data[CONF_RAINBIRD_ENTRY_ID]
        )
        rainbird_entities = (
            er.async_entries_for_config_entry(registry, rainbird_entry.entry_id)
            if rainbird_entry
            else []
        )
        extended_entities = er.async_entries_for_config_entry(registry, entry.entry_id)
        irrigating_entity = next(
            (
                entity.entity_id
                for entity in extended_entities
                if entity.domain == "binary_sensor"
                and entity.unique_id.endswith("-irrigating")
            ),
            None,
        )
        rainbird_status_entities = [
            entity.entity_id
            for entity in rainbird_entities
            if entity.disabled_by is None
        ]
        rain_delay_entity = next(
            (
                entity
                for entity in rainbird_entities
                if entity.domain == "number"
                and entity.disabled_by is None
                and entity.unique_id.endswith("-rain-delay")
            ),
            None,
        )
        rain_delay = None
        if rain_delay_entity is not None:
            state = hass.states.get(rain_delay_entity.entity_id)
            rain_delay = {
                "entity_id": rain_delay_entity.entity_id,
                "name": "Rain delay",
                "value": state.state if state else "unknown",
                "min": state.attributes.get("min", 0) if state else 0,
                "max": state.attributes.get("max", 14) if state else 14,
                "step": state.attributes.get("step", 1) if state else 1,
                "unit": state.attributes.get("unit_of_measurement", "d")
                if state
                else "d",
            }
        watering_entities = sorted(
            [
                entity.entity_id
                for entity in rainbird_entities
                if entity.domain == "switch" and entity.disabled_by is None
            ]
            + [
                entity.entity_id
                for entity in extended_entities
                if entity.domain == "valve"
            ]
        )
        numbers_by_zone: dict[int, list[dict[str, Any]]] = {}
        zone_entities: dict[int, dict[str, str]] = {}
        zone_names = _zone_names(
            hass, device_registry, rainbird_entities, extended_entities
        )
        zone_ids: set[int] = set()
        stop_button = None
        for entity in extended_entities:
            if (
                entity.domain == "button"
                and entity.disabled_by is None
                and entity.unique_id.endswith("-stop_irrigation")
            ):
                stop_button = entity.entity_id
                continue
            if entity.domain == "valve" and entity.disabled_by is None:
                parts = entity.unique_id.rsplit("-", 2)
                if len(parts) == 3 and parts[1].isdigit() and parts[2] == "valve":
                    zone = int(parts[1])
                    zone_ids.add(zone)
                    zone_entities.setdefault(zone, {})["valve"] = entity.entity_id
                continue
            if entity.domain == "sensor" and entity.disabled_by is None:
                parts = entity.unique_id.rsplit("-", 2)
                if (
                    len(parts) == 3
                    and parts[1].isdigit()
                    and parts[2] in ("time_remaining", "last_run")
                ):
                    zone_entities.setdefault(int(parts[1]), {})[parts[2]] = (
                        entity.entity_id
                    )
                continue
            if entity.domain != "number" or entity.disabled_by is not None:
                continue
            parts = entity.unique_id.rsplit("-", 2)
            if len(parts) != 3 or not parts[1].isdigit():
                continue
            state = hass.states.get(entity.entity_id)
            if state is None:
                continue
            zone = int(parts[1])
            zone_ids.add(zone)
            numbers_by_zone.setdefault(zone, []).append(
                {
                    "entity_id": entity.entity_id,
                    "name": state.attributes.get("friendly_name", entity.entity_id),
                    "value": state.state,
                    "min": state.attributes.get("min"),
                    "max": state.attributes.get("max"),
                    "step": state.attributes.get("step"),
                    "unit": state.attributes.get("unit_of_measurement", ""),
                }
            )
        zones = [
            {
                "zone": zone,
                "name": zone_names.get(zone) or f"Zone {zone}",
                "valve_entity": zone_entities.get(zone, {}).get("valve"),
                "time_remaining_entity": zone_entities.get(zone, {}).get(
                    "time_remaining"
                ),
                "last_run_entity": zone_entities.get(zone, {}).get("last_run"),
                "numbers": sorted(
                    numbers_by_zone.get(zone, []), key=lambda item: item["entity_id"]
                ),
            }
            for zone in sorted(zone_ids)
        ]
        coordinator = getattr(entry, "runtime_data", None)
        program_count = min(getattr(coordinator, "max_programs", 0) or 0, MAX_PROGRAMS)
        controllers.append(
            {
                "entry_id": entry.entry_id,
                "title": entry.title,
                "state": entry.state.value,
                "rainbird_title": rainbird_entry.title if rainbird_entry else None,
                "rainbird_loaded": bool(
                    rainbird_entry and rainbird_entry.state is ConfigEntryState.LOADED
                ),
                "rainbird_status_entities": rainbird_status_entities,
                "irrigating_entity": irrigating_entity,
                "watering_entities": watering_entities,
                "weather_entity": entry.options.get(CONF_WEATHER_ENTITY),
                "rain_delay": rain_delay,
                "program_count": program_count,
                "stop_action": entry.options.get(CONF_STOP_ACTION, DEFAULT_STOP_ACTION),
                "programs": _program_summaries(
                    entry.options,
                    program_count,
                    coordinator,
                    dt_util.now(),
                ),
                "stop_button": stop_button,
                # On/off entity that says whether the controller is turned on.
                "power_entity": entry.options.get(CONF_POWER_ENTITY),
                "hidden_zones": (
                    hidden := _hidden_zones(
                        device_registry,
                        entry.entry_id,
                        rainbird_entry.unique_id if rainbird_entry else None,
                    )
                ),
                "zones": [
                    zone
                    for zone in zones
                    if zone["zone"] not in {item["zone"] for item in hidden}
                ],
            }
        )
    return {"controllers": controllers}


def _version() -> str:
    """Read the integration version used to bust the browser cache."""
    return json.loads((Path(__file__).parent / "manifest.json").read_text())["version"]


async def async_register_panel(hass: HomeAssistant) -> None:
    """Register the sidebar panel, frontend module, and its data command."""
    async with hass.data.setdefault(_PANEL_LOCK, asyncio.Lock()):
        if hass.data.get(_PANEL_REGISTERED):
            return
        if "frontend" not in hass.config.components or hass.http is None:
            _LOGGER.debug("Frontend not loaded; skipping the Rain Bird Extended panel")
            return

        async_setup_panel_api(hass)
        if not hass.data.get(_STATIC_REGISTERED):
            version = await hass.async_add_executor_job(_version)
            await hass.http.async_register_static_paths(
                [StaticPathConfig(STATIC_URL, str(FRONTEND_DIR), cache_headers=True)]
            )
            hass.data[_PANEL_VERSION] = version
            hass.data[_STATIC_REGISTERED] = True

        await panel_custom.async_register_panel(
            hass,
            frontend_url_path=PANEL_URL_PATH,
            webcomponent_name=PANEL_COMPONENT,
            sidebar_title="Rain Bird Extended",
            sidebar_icon="mdi:sprinkler-variant",
            module_url=(
                f"{STATIC_URL}/rainbird-extended-panel.js?v={hass.data[_PANEL_VERSION]}"
            ),
            require_admin=True,
            config={},
        )
        hass.data[_PANEL_REGISTERED] = True


async def async_unregister_panel(
    hass: HomeAssistant, *, excluding_entry_id: str | None = None
) -> None:
    """Remove the panel only when no other Rain Bird Extended entry is loaded."""
    async with hass.data.setdefault(_PANEL_LOCK, asyncio.Lock()):
        if not hass.data.get(_PANEL_REGISTERED):
            return
        if excluding_entry_id is not None and any(
            entry.entry_id != excluding_entry_id
            and entry.state
            in (ConfigEntryState.LOADED, ConfigEntryState.SETUP_IN_PROGRESS)
            for entry in hass.config_entries.async_entries(DOMAIN)
        ):
            return
        if "frontend" in hass.config.components:
            frontend.async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)
        hass.data[_PANEL_REGISTERED] = False
