"""Admin-only sidebar panel for configuring Rain Bird Extended."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .config_flow import MAX_PROGRAMS
from .const import CONF_RAINBIRD_ENTRY_ID, DOMAIN

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
    connection.send_result(msg["id"], _panel_config(hass))


def _panel_config(hass: HomeAssistant) -> dict[str, list[dict[str, Any]]]:
    """Build a dashboard model without exposing Rain Bird credentials."""
    registry = er.async_get(hass)
    controllers: list[dict[str, Any]] = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        rainbird_entry = hass.config_entries.async_get_entry(
            entry.data[CONF_RAINBIRD_ENTRY_ID]
        )
        numbers_by_zone: dict[int, list[dict[str, Any]]] = {}
        zone_ids: set[int] = set()
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
            if entity.domain == "valve" and entity.disabled_by is None:
                parts = entity.unique_id.rsplit("-", 2)
                if len(parts) == 3 and parts[1].isdigit() and parts[2] == "valve":
                    zone_ids.add(int(parts[1]))
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
                "numbers": sorted(
                    numbers_by_zone.get(zone, []), key=lambda item: item["entity_id"]
                ),
            }
            for zone in sorted(zone_ids)
        ]
        coordinator = getattr(entry, "runtime_data", None)
        controllers.append(
            {
                "entry_id": entry.entry_id,
                "title": entry.title,
                "state": entry.state.value,
                "rainbird_title": rainbird_entry.title if rainbird_entry else None,
                "rainbird_available": bool(
                    rainbird_entry and rainbird_entry.state is ConfigEntryState.LOADED
                ),
                "program_count": min(
                    getattr(coordinator, "max_programs", 0) or 0, MAX_PROGRAMS
                ),
                "zones": zones,
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
                [StaticPathConfig(STATIC_URL, str(FRONTEND_DIR), cache_headers=False)]
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
