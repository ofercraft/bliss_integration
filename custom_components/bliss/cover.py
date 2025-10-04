"""Cover platform for Bliss blinds."""
from __future__ import annotations

from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverEntity,
    CoverEntityDescription,
    CoverEntityFeature,
)
from homeassistant.const import CONF_DEVICES

from .const import DOMAIN, LOGGER
from .coordinator import BlissBlindCoordinator
from .entity import BlissBaseEntity

COVER_DESCRIPTION = CoverEntityDescription(
    key="cover",
    name="Blind",
)


async def async_setup_entry(hass, config_entry, async_add_entities):
    entities: list[BlissCoverEntity] = []

    for device_id, _conf in config_entry.data.get(CONF_DEVICES, {}).items():
        coordinator: BlissBlindCoordinator = hass.data[DOMAIN][CONF_DEVICES][device_id]
        entities.append(BlissCoverEntity(coordinator, COVER_DESCRIPTION))

    async_add_entities(entities)


class BlissCoverEntity(BlissBaseEntity, CoverEntity):
    _attr_supported_features = (
        CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.SET_POSITION
    )

    def __init__(
        self, coordinator: BlissBlindCoordinator, description: CoverEntityDescription
    ) -> None:
        super().__init__(coordinator, description)
        self._attr_should_poll = False

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data.get("available"))

    @property
    def current_cover_position(self) -> int | None:
        return self.coordinator.data.get("position")

    @property
    def is_closed(self) -> bool | None:
        position = self.current_cover_position
        if position is None:
            return None
        return position <= 0

    async def async_open_cover(self, **kwargs: Any) -> None:
        LOGGER.debug("Opening Bliss blind %s", self.coordinator.address)
        await self.coordinator.async_open()

    async def async_close_cover(self, **kwargs: Any) -> None:
        LOGGER.debug("Closing Bliss blind %s", self.coordinator.address)
        await self.coordinator.async_close()

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        if ATTR_POSITION not in kwargs:
            return
        position = int(kwargs[ATTR_POSITION])
        position = max(0, min(100, position))
        LOGGER.debug("Setting Bliss blind %s to %s%%", self.coordinator.address, position)
        await self.coordinator.async_set_percentage(position)
