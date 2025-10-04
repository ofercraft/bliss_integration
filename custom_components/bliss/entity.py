"""Base entity class for the Bliss integration."""
from __future__ import annotations

from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BlissBlindCoordinator


class BlissBaseEntity(CoordinatorEntity[BlissBlindCoordinator]):
    """Common base entity for Bliss devices."""

    def __init__(self, coordinator: BlissBlindCoordinator, description: EntityDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description

        self._attr_name = f"{self.coordinator.device_name} {description.name}"
        self._attr_unique_id = f"{self.coordinator.address}-{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, self.coordinator.address)},
            "name": self.coordinator.device_name,
        }
