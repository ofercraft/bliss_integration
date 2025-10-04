"""Coordinator for Bliss blind devices."""
from __future__ import annotations

from datetime import timedelta

from bleak.exc import BleakError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .bliss_bt_client import BlissBlindClient
from .const import (
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_RANGE_MAX,
    DEFAULT_PASSWORD,
    DEFAULT_RANGE_MAX,
    LOGGER,
)


class BlissBlindCoordinator(DataUpdateCoordinator[dict]):
    """Handle communication between Home Assistant and a Bliss blind."""

    def __init__(self, hass, device_id: str, conf: dict) -> None:
        self.device_id = device_id
        self.device_name = conf.get(CONF_NAME, device_id)
        self.address = conf[CONF_MAC]
        self.password = conf.get(CONF_PASSWORD, DEFAULT_PASSWORD)
        self.range_max = int(conf.get(CONF_RANGE_MAX, DEFAULT_RANGE_MAX))

        super().__init__(
            hass,
            LOGGER,
            name=f"Bliss Blind: {self.device_name}",
            update_interval=timedelta(minutes=5),
        )

        self._client = BlissBlindClient(
            hass,
            self.address,
            self.password,
            self.range_max,
            self._handle_status_update,
        )

        self.data = {
            "available": False,
            "position": None,
            "raw_position": None,
        }

    async def _handle_status_update(self, state: dict) -> None:
        self.data.update(state)
        self.async_set_updated_data(self.data)

    async def _async_update_data(self) -> dict:
        try:
            await self._client.refresh_status()
        except BleakError as err:
            raise UpdateFailed(f"Bluetooth error while updating {self.address}") from err
        self.data.update(self._client.state)
        return self.data

    async def async_set_fraction(self, fraction: float) -> None:
        await self._client.set_cover_fraction(fraction)
        self.data.update(self._client.state)
        self.async_set_updated_data(self.data)

    async def async_set_percentage(self, percentage: int) -> None:
        await self._client.set_cover_percentage(percentage)
        self.data.update(self._client.state)
        self.async_set_updated_data(self.data)

    async def async_open(self) -> None:
        await self._client.open_cover()
        self.data.update(self._client.state)
        self.async_set_updated_data(self.data)

    async def async_close(self) -> None:
        await self._client.close_cover()
        self.data.update(self._client.state)
        self.async_set_updated_data(self.data)

    async def async_shutdown(self) -> None:
        await self._client.disconnect()
        await super().async_shutdown()
