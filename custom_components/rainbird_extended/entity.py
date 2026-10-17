"""Base entities for Rain Bird Extended."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import UnitOfVolume, UnitOfVolumeFlowRate
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from .const import DOMAIN, LCR_BUDGET, RAINBIRD_DOMAIN
from .coordinator import RainbirdExtendedCoordinator


class RainbirdExtendedEntity(CoordinatorEntity[RainbirdExtendedCoordinator]):
    """An entity on Rain Bird Extended's device for a Rain Bird controller or zone.

    The devices are made at setup (see devices.py), linked to Rain Bird's.
    """

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: RainbirdExtendedCoordinator, device_uid: str, key: str
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{device_uid}-{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, device_uid)})

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
        )


class RainbirdExtendedControllerEntity(RainbirdExtendedEntity):
    """An entity on the controller's device."""

    def __init__(self, coordinator: RainbirdExtendedCoordinator, key: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator, str(coordinator.rainbird_unique_id), key)


class RainbirdExtendedZoneEntity(RainbirdExtendedEntity):
    """An entity on a zone's device."""

    def __init__(
        self, coordinator: RainbirdExtendedCoordinator, zone: int, key: str
    ) -> None:
        """Initialize the entity."""
        self._zone = zone
        super().__init__(coordinator, f"{coordinator.rainbird_unique_id}-{zone}", key)

    @property
    def available(self) -> bool:
        """Available while the controller is reachable and has this zone."""
        return super().available and self._zone in self.coordinator.zones


def find_device(
    hass: HomeAssistant, rainbird_entry_id: str, device_uid: str
) -> dr.DeviceEntry | None:
    """Find a device created by the core Rain Bird integration."""
    # Identifiers are scoped to a config entry (Home Assistant 2026.9+).
    return dr.async_get(hass).async_get_device_by_identifier(
        (RAINBIRD_DOMAIN, device_uid), rainbird_entry_id
    )


def water_budget_naming(key: int) -> tuple[str, str, dict[str, str]]:
    """Unique id key, translation key and placeholders for a seasonal adjust."""
    if key == LCR_BUDGET:
        return "seasonal_adjustment", "seasonal_adjustment", {}
    letter = chr(ord("A") + key)
    return (
        f"seasonal_adjustment_{letter.lower()}",
        "seasonal_adjustment_program",
        {"program": letter},
    )


def water_units(hass: HomeAssistant) -> tuple[str, str]:
    """Flow rate and volume units for the configured unit system."""
    if hass.config.units is US_CUSTOMARY_SYSTEM:
        return UnitOfVolumeFlowRate.GALLONS_PER_MINUTE, UnitOfVolume.GALLONS
    return UnitOfVolumeFlowRate.LITERS_PER_MINUTE, UnitOfVolume.LITERS
