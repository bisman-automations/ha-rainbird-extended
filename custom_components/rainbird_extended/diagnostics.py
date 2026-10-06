"""Diagnostics for Rain Bird Extended."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import RainbirdExtendedConfigEntry

# The controller's unique id is its MAC address.
TO_REDACT = {"unique_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: RainbirdExtendedConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    rainbird_entry = coordinator.rainbird_entry
    model = getattr(coordinator.rainbird_data, "model_info", None)
    end_times = coordinator.data or {}
    zones = sorted(coordinator.zones)

    return {
        "rainbird_entry": async_redact_data(
            {
                "title": rainbird_entry.title,
                "state": rainbird_entry.state.value,
                "unique_id": rainbird_entry.unique_id,
                "default_duration_minutes": coordinator.default_runtime // 60,
            },
            TO_REDACT,
        ),
        "controller": {
            "model": model.model_name if model else None,
            "model_code": model.model_code if model else None,
            "firmware": f"{model.major}.{model.minor}" if model else None,
            # None = not asked yet,
            # False = controller does not report remaining run time.
            "supports_remaining_runtime": coordinator.supports_controller_state,
            "seasonal_adjust": (
                coordinator.controller_state.seasonal_adjust
                if coordinator.controller_state
                else None
            ),
            "max_programs": coordinator.max_programs,
            "supports_water_budget": coordinator.supports_water_budget,
            "can_set_water_budget": coordinator.can_set_water_budget,
            "water_budgets": {
                str(key): value
                for key, value in sorted(coordinator.water_budgets.items())
            },
        },
        "options": dict(entry.options),
        "schedule_loaded": bool(coordinator.schedule and coordinator.schedule.data),
        "core_coordinator": {
            "last_update_success": coordinator.rainbird.last_update_success,
            "rain": getattr(coordinator.rainbird.data, "rain", None),
            "rain_delay": getattr(coordinator.rainbird.data, "rain_delay", None),
        },
        "zones": zones,
        "linked_zones": coordinator.linked_zones,
        "active_zones": sorted(coordinator.active_zones),
        "runtimes_seconds": {
            str(zone): coordinator.runtime_for(zone) for zone in zones
        },
        "flow_rates": {
            str(zone): coordinator.flow_rates.get(zone, 0.0) for zone in zones
        },
        "next_runs": {
            str(zone): next_run.isoformat()
            for zone in zones
            if (next_run := coordinator.next_run(zone))
        },
        "last_runs": {
            str(zone): {
                "start": run.start.isoformat(),
                "end": run.end.isoformat() if run.end else None,
            }
            for zone, run in sorted(coordinator.last_runs.items())
        },
        "run_seconds": {
            str(zone): round(seconds)
            for zone, seconds in sorted(coordinator.run_seconds.items())
        },
        "run_all_zones_active": coordinator.sequence_running,
        "end_times": {
            str(zone): end.isoformat() if end else None
            for zone, end in sorted(end_times.items())
        },
        "local_runs": {
            str(zone): {
                "started_at": run.started_at.isoformat(),
                "end": run.end.isoformat(),
                "seen_active": run.seen_active,
            }
            for zone, run in sorted(coordinator.local_runs.items())
        },
        "last_update_success": coordinator.last_update_success,
    }
