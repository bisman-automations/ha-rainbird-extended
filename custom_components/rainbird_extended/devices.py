"""Rain Bird Extended's own devices, linked to the Rain Bird ones.

Home Assistant 2026.9 keeps each integration's devices separate and lists
devices that share hardware as "Linked devices". Rain Bird Extended has its
own service device for the controller and for each zone, holding its
entities:

- the controller's shares the Rain Bird controller's MAC address, so it's
  linked to it (and to other integrations' devices for the same hardware,
  such as a UniFi client);
- each zone's carries the Rain Bird zone's identifier too, so it's linked to
  that zone, and is connected through Rain Bird Extended's controller device.

They take their names (and, when first made, their areas) from the Rain Bird
devices, so entity names stay the same as when they lived on those devices.
They also carry the Rain Bird device's manufacturer, model and firmware, since
other integrations (HomeKit Extended) read those from a valve's device.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, RAINBIRD_DOMAIN
from .entity import find_device

MANUFACTURER = "Rain Bird"


def _name(device: dr.DeviceEntry | None, fallback: str) -> str:
    if device is not None and (name := device.name_by_user or device.name):
        return name
    return fallback


@callback
def async_sync_devices(
    hass: HomeAssistant,
    entry: ConfigEntry,
    rainbird_entry: ConfigEntry,
    zones: list[int],
) -> set[str]:
    """Create or update the controller and zone devices; remove stale ones.

    Returns the ids of the Rain Bird devices they're linked to.
    """
    registry = dr.async_get(hass)
    uid = str(rainbird_entry.unique_id)
    linked: set[str] = set()

    def ensure(
        device_uid: str,
        source: dr.DeviceEntry | None,
        fallback: str,
        **info: object,
    ) -> dr.DeviceEntry:
        existed = registry.async_get_device_by_identifier(
            (DOMAIN, device_uid), entry.entry_id
        )
        device = registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            name=_name(source, fallback),
            manufacturer=(source.manufacturer if source else None) or MANUFACTURER,
            model=source.model if source else None,
            sw_version=source.sw_version if source else None,
            entry_type=dr.DeviceEntryType.SERVICE,
            **info,  # type: ignore[arg-type]
        )
        if source is not None:
            linked.add(source.id)
            if existed is None and device.area_id is None and source.area_id:
                device = (
                    registry.async_update_device(device.id, area_id=source.area_id)
                    or device
                )
        return device

    controller = find_device(hass, rainbird_entry.entry_id, uid)
    own_controller = ensure(
        uid,
        controller,
        rainbird_entry.title,
        identifiers={(DOMAIN, uid)},
        connections=set(controller.connections) if controller else set(),
    )
    if own_controller.via_device_id is not None:
        # 1.8.0 connected it through the Rain Bird controller; it's linked now.
        registry.async_update_device(own_controller.id, via_device_id=None)

    wanted = {own_controller.id}
    for zone in zones:
        zone_uid = f"{uid}-{zone}"
        device = ensure(
            zone_uid,
            find_device(hass, rainbird_entry.entry_id, zone_uid),
            f"Zone {zone}",
            identifiers={(DOMAIN, zone_uid), (RAINBIRD_DOMAIN, zone_uid)},
            via_device_id=own_controller.id,
        )
        wanted.add(device.id)

    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if device.id not in wanted:
            registry.async_remove_device(device.id)
    return linked


@callback
def async_follow_renames(
    hass: HomeAssistant,
    entry: ConfigEntry,
    rainbird_entry: ConfigEntry,
    zones: list[int],
    linked: set[str],
) -> None:
    """Keep the names in step when a Rain Bird device is renamed."""

    @callback
    def _renamed(event: Event[dr.EventDeviceRegistryUpdatedData]) -> None:
        data = event.data
        if (
            data["action"] == "update"
            and data["device_id"] in linked
            and {"name", "name_by_user"} & set(data.get("changes", {}))
        ):
            async_sync_devices(hass, entry, rainbird_entry, zones)

    entry.async_on_unload(
        hass.bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, _renamed)
    )
