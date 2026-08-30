"""Coordinator for Bliss blind devices."""
from __future__ import annotations

import asyncio
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

STATUS_POLL_INTERVAL = timedelta(seconds=3)
MOTION_TRACK_INTERVAL = 1   # seconds between tracking polls
MOTION_TRACK_TIMEOUT = 60   # max seconds to poll after a position change
MOTION_STABLE_COUNT = 2     # consecutive same-position reads = blind stopped


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
            update_interval=STATUS_POLL_INTERVAL,
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
            "battery_percentage": None,
            "battery_voltage_mv": None,
            "battery_status": None,
            "battery_status_code": None,
            "battery_percentage_source": None,
            "battery_raw_response": None,
        }
        self._motion_task: asyncio.Task | None = None

    def _handle_status_update(self, state: dict) -> None:
        prev_pos = self.data.get("position")
        self.data.update(state)
        new_pos = self.data.get("position")
        self.async_set_updated_data(self.data)
        # When position changes and we're not already tracking, start rapid polling
        # so RF-remote / Bliss-app moves are tracked to completion.
        if new_pos != prev_pos and not (self._motion_task and not self._motion_task.done()):
            self._motion_task = self.hass.async_create_task(
                self._motion_tracking_loop()
            )

    async def _motion_tracking_loop(self) -> None:
        """Poll every 2 s after a position change until the blind stops moving."""
        last_pos = self.data.get("position")
        stable = 0
        for _ in range(MOTION_TRACK_TIMEOUT // MOTION_TRACK_INTERVAL):
            await asyncio.sleep(MOTION_TRACK_INTERVAL)
            try:
                await self._client.refresh_status()
            except Exception:
                LOGGER.debug("Motion tracking poll failed for %s", self.device_id)
                continue
            current_pos = self.data.get("position")
            if current_pos == last_pos:
                stable += 1
                if stable >= MOTION_STABLE_COUNT:
                    break
            else:
                stable = 0
            last_pos = current_pos

    async def _async_update_data(self) -> dict:
        try:
            await self._client.refresh_status()
            await self._client.refresh_battery_status()
        except (BleakError, TimeoutError) as err:
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

    async def async_stop(self) -> None:
        await self._client.stop_cover()
        self.data.update(self._client.state)
        self.async_set_updated_data(self.data)

    async def async_shutdown(self) -> None:
        if self._motion_task and not self._motion_task.done():
            self._motion_task.cancel()
        await self._client.close()
        await super().async_shutdown()
