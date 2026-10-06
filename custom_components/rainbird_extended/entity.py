"""Base entity for Rain Bird Extended zone entities."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import RAINBIRD_DOMAIN
from .coordinator import RainbirdExtendedCoordinator


class RainbirdExtendedZoneEntity(CoordinatorEntity[RainbirdExtendedCoordinator]):
    """An entity attached to a zone device owned by the core Rain Bird integration."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: RainbirdExtendedCoordinator, zone: int, key: str
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._zone = zone
        rainbird_uid = coordinator.rainbird_unique_id
        self._attr_unique_id = f"{rainbird_uid}-{zone}-{key}"
        # Attach to the zone device the core Rain Bird integration owns,
        # without creating a device of our own or changing its details.
        self.device_entry = find_zone_device(
            coordinator.hass,
            coordinator.rainbird_entry.entry_id,
            f"{rainbird_uid}-{zone}",
        )

    async def async_added_to_hass(self) -> None:
        """Also follow the core coordinator so zone state stays in sync."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.rainbird.async_add_listener(
                self._handle_coordinator_update
            )
        )

    @property
    def available(self) -> bool:
        """Available while the core integration can reach the controller."""
        return (
            self.coordinator.rainbird_entry.state is ConfigEntryState.LOADED
            and self.coordinator.rainbird.last_update_success
            and self._zone in self.coordinator.zones
        )


def find_zone_device(
    hass: HomeAssistant, rainbird_entry_id: str, zone_unique_id: str
) -> dr.DeviceEntry | None:
    """Find the zone device created by the core Rain Bird integration."""
    registry = dr.async_get(hass)
    identifier = (RAINBIRD_DOMAIN, zone_unique_id)
    if hasattr(registry, "async_get_device_by_identifier"):
        # Home Assistant 2026.9+: identifiers are scoped to a config entry.
        return registry.async_get_device_by_identifier(identifier, rainbird_entry_id)
    return registry.async_get_device(identifiers={identifier})
