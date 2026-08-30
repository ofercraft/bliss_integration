"""Sensor platform for Bliss blinds."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE

from .const import DOMAIN
from .coordinator import BlissBlindCoordinator
from .entity import BlissBaseEntity

BATTERY_DESCRIPTION = SensorEntityDescription(
    key="battery",
    name="Battery",
    device_class=SensorDeviceClass.BATTERY,
    native_unit_of_measurement=PERCENTAGE,
    state_class=SensorStateClass.MEASUREMENT,
)


async def async_setup_entry(hass, config_entry, async_add_entities):
    entities: list[BlissBatterySensor] = []

    for device_id, _conf in config_entry.data.get("devices", {}).items():
        coordinator: BlissBlindCoordinator = hass.data[DOMAIN]["devices"][device_id]
        entities.append(BlissBatterySensor(coordinator, BATTERY_DESCRIPTION))

    async_add_entities(entities)


class BlissBatterySensor(BlissBaseEntity, SensorEntity):
    """Battery percentage sensor for a Bliss blind."""

    @property
    def native_value(self) -> int | None:
        return self.coordinator.data.get("battery_percentage")

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "status": self.coordinator.data.get("battery_status"),
            "status_code": self.coordinator.data.get("battery_status_code"),
            "percentage_source": self.coordinator.data.get("battery_percentage_source"),
            "voltage_mv": self.coordinator.data.get("battery_voltage_mv"),
            "raw_response": self.coordinator.data.get("battery_raw_response"),
        }
