"""Bluetooth client helper for Bliss blinds."""
from __future__ import annotations

import asyncio
import struct
from collections.abc import Callable
from datetime import datetime

import async_timeout
from bleak import BleakClient, BleakGATTCharacteristic, BLEDevice
from bleak.exc import BleakError

from homeassistant.components import bluetooth

from .const import BLISS_NAME_PATTERN, LOGGER

COMMAND_UUID = "00010405-0405-0607-0809-0a0b0c0d1910"
RESPONSE_UUID = "00010304-0405-0607-0809-0a0b0c0d1910"

PWD_PREFIX = bytes([0xFF, 0x03, 0x03, 0x03, 0x03])
GOTO_PREFIX = bytes([0xFF, 0x78, 0xEA, 0x41, 0xBF, 0x03])
SET_TIME_PREFIX = bytes([0xFF, 0x78, 0xEA, 0x41, 0x02, 0x00])
READ_STATUS = bytes([0xFF, 0x78, 0xEA, 0x41, 0xD1, 0x03, 0x01])


def build_login(password: str) -> bytes:
    data = password.encode("utf-8")
    data = (data + b"\x00" * 6)[:6]
    return PWD_PREFIX + data


def build_set_time(now: datetime | None = None) -> bytes:
    now = now or datetime.now()
    payload = bytes(
        [
            (now.year - 2000) & 0xFF,
            now.month & 0xFF,
            now.day & 0xFF,
            now.hour & 0xFF,
            now.minute & 0xFF,
            now.second & 0xFF,
        ]
    )
    return SET_TIME_PREFIX + payload


def build_move_command(position: int, *, prefix: bytes = GOTO_PREFIX) -> bytes:
    if not 0 <= position <= 0xFFFF:
        raise ValueError(f"Position {position} is outside 0..65535")
    return prefix + struct.pack("<H", position)


class BlissBlindClient:
    """Small helper around a BLE connection to a Bliss blind."""

    def __init__(
        self,
        hass,
        address: str,
        password: str,
        range_max: int,
        status_callback: Callable[[dict], None] | None = None,
    ) -> None:
        self._hass = hass
        self._address = address.upper()
        self._password = password
        self._range_max = range_max
        self._status_callback = status_callback

        self._ble_device: BLEDevice | None = None
        self._bt_client: BleakClient | None = None

        self._position_device_units: int | None = None
        self._available = False
        self._busy = asyncio.Lock()
        self._status_event = asyncio.Event()

    @property
    def available(self) -> bool:
        return self._available

    @property
    def position_percentage(self) -> int | None:
        if self._position_device_units is None:
            return None
        try:
            percent = 100 - round(
                (self._position_device_units / self._range_max) * 100
            )
        except ZeroDivisionError:
            percent = 0
        return max(0, min(100, percent))

    async def ensure_connected(self) -> None:
        if self._bt_client and self._bt_client.is_connected:
            return
        await self._connect()

    async def _connect(self) -> None:
        if self._bt_client and self._bt_client.is_connected:
            return

        ble_device = bluetooth.async_ble_device_from_address(
            self._hass, self._address, connectable=True
        )
        if ble_device:
            self._ble_device = ble_device
        if not self._ble_device:
            raise BleakError(f"Unable to find device with address {self._address}")

        LOGGER.debug("Connecting to Bliss blind %s", self._address)
        self._bt_client = BleakClient(self._ble_device)
        try:
            await self._bt_client.connect()
            # Ensure the Bluetooth stack resolves services before we attempt
            # to interact with the blind. BlueZ occasionally reports a
            # successful connection while the characteristic cache is still
            # empty, which leads to sporadic "Characteristic ... was not
            # found" errors when we try to subscribe or write immediately.
            await self._bt_client.get_services()
        except BleakError as err:
            self._bt_client = None
            raise err

        await self._bt_client.start_notify(RESPONSE_UUID, self._notification_handler)
        await self._initialize()
        self._available = True

    async def disconnect(self) -> None:
        client = self._bt_client
        self._bt_client = None
        if client and client.is_connected:
            LOGGER.debug("Disconnecting Bliss blind %s", self._address)
            try:
                await client.stop_notify(RESPONSE_UUID)
            except BleakError:
                pass
            try:
                await client.disconnect()
            except BleakError:
                pass
        self._available = False

    async def _initialize(self) -> None:
        await self._send_command(build_login(self._password), "login")
        await asyncio.sleep(0.1)
        await self._send_command(build_set_time(), "set_clock")

    async def refresh_status(self) -> None:
        await self.ensure_connected()
        self._status_event.clear()
        await self._send_command(READ_STATUS, "read_status")
        try:
            async with async_timeout.timeout(2):
                await self._status_event.wait()
        except asyncio.TimeoutError:
            LOGGER.debug("Timeout waiting for status from %s", self._address)

    async def set_cover_fraction(self, fraction: float) -> None:
        fraction = max(0.0, min(1.0, fraction))
        await self.ensure_connected()
        device_position = round(self._range_max * (1.0 - fraction))
        await self._send_command(build_move_command(device_position), "move")
        self._position_device_units = device_position
        if self._status_callback:
            self._status_callback(self._build_state())

    async def set_cover_percentage(self, percentage: int) -> None:
        await self.set_cover_fraction(percentage / 100)

    async def open_cover(self) -> None:
        await self.set_cover_fraction(1.0)

    async def close_cover(self) -> None:
        await self.set_cover_fraction(0.0)

    def _build_state(self) -> dict:
        return {
            "available": self._available,
            "position": self.position_percentage,
            "raw_position": self._position_device_units,
        }

    @property
    def state(self) -> dict:
        """Return the latest known state dictionary."""
        return self._build_state()

    async def _send_command(self, data: bytes, label: str) -> None:
        if not self._bt_client:
            raise BleakError("Not connected")
        async with self._busy:
            LOGGER.debug("→ %s: %s", label, data.hex(" "))
            await self._bt_client.write_gatt_char(COMMAND_UUID, data, response=True)
            await asyncio.sleep(0.1)

    def _notification_handler(
        self, characteristic: BleakGATTCharacteristic, data: bytearray
    ) -> None:
        LOGGER.debug(
            "← notify %s (%s): %s",
            self._address,
            characteristic.uuid,
            data.hex(" "),
        )
        if len(data) >= 8 and data[0:4] == b"\xFFx\xEA\x41":
            command = data[4]
            if command == 0xD1 and len(data) >= 9:
                position = int.from_bytes(data[6:8], "little", signed=False)
                self._position_device_units = position
                if self._status_callback:
                    self._status_callback(self._build_state())
                self._status_event.set()
        # Some devices acknowledge movement on a different opcode
        if len(data) >= 8 and data[0:4] == b"\xFFx\xEA\x41" and data[4] == 0xBF:
            if len(data) >= 9:
                position = int.from_bytes(data[6:8], "little", signed=False)
                self._position_device_units = position
                if self._status_callback:
                    self._status_callback(self._build_state())

    @staticmethod
    async def async_discover(timeout: float = 10.0) -> list[tuple[str, str]]:
        """Discover nearby Bliss blinds using Bluetooth information from HA."""
        infos = bluetooth.async_discovered_service_info()
        matches: list[tuple[str, str]] = []
        for info in infos:
            if not info.connectable:
                continue
            name = info.name or ""
            if BLISS_NAME_PATTERN.match(name):
                matches.append((name, info.address))
        return matches
