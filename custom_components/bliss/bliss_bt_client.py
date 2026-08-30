"""Bluetooth client helper for Bliss blinds."""
from __future__ import annotations

import asyncio
import struct
from collections.abc import Callable
from datetime import datetime, timedelta

import async_timeout
from bleak import BleakClient, BleakGATTCharacteristic, BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import establish_connection

from homeassistant.components import bluetooth

from .const import BLISS_NAME_PATTERN, LOGGER

COMMAND_UUID = "00010405-0405-0607-0809-0a0b0c0d1910"
RESPONSE_UUID = "00010304-0405-0607-0809-0a0b0c0d1910"

PWD_PREFIX = bytes([0xFF, 0x03, 0x03, 0x03, 0x03])
GOTO_PREFIX = bytes([0xFF, 0x78, 0xEA, 0x41, 0xBF, 0x03])
SET_TIME_PREFIX = bytes([0xFF, 0x78, 0xEA, 0x41, 0x02, 0x00])
READ_STATUS = bytes([0xFF, 0x78, 0xEA, 0x41, 0xD1, 0x03, 0x01])
READ_BATTERY = bytes([0xFF, 0x78, 0xEA, 0x41, 0xF0, 0x03, 0x01])

BATTERY_REFRESH_INTERVAL = timedelta(hours=6)
# A command lands on the existing link only if we've had a successful exchange
# this recently; otherwise we reconnect first, because the blind silently sleeps
# its connection and a stale "connected" socket swallows the command as a timeout.
COMMAND_FRESH_WINDOW = timedelta(seconds=6)
STATUS_RESPONSE_TIMEOUT = 1.5
BATTERY_RESPONSE_TIMEOUT = 3.0
OPERATION_ATTEMPTS = 3
RETRY_DELAY = 0.25
BATTERY_PERCENTAGE_SOURCE = "bliss_app_status_mapping"
BATTERY_FULL = (0, "full", 100)
BATTERY_LOW = (1, "low", 25)
BATTERY_EMPTY = (2, "empty", 0)
BATTERY_UNKNOWN = (-1, "unknown", None)


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
        self._battery_percentage: int | None = None
        self._battery_voltage_mv: int | None = None
        self._battery_status: str | None = None
        self._battery_status_code: int | None = None
        self._battery_percentage_source: str | None = None
        self._battery_raw_response: str | None = None
        self._last_battery_refresh: datetime | None = None
        self._available = False
        self._last_ok: datetime | None = None
        self._busy = asyncio.Lock()
        self._conn_lock = asyncio.Lock()
        self._status_lock = asyncio.Lock()
        self._status_event = asyncio.Event()
        self._battery_event = asyncio.Event()
        self._recovery_task: asyncio.Task[None] | None = None
        self._closing = False

    @property
    def available(self) -> bool:
        return self._available

    @property
    def position_percentage(self) -> int | None:
        if self._position_device_units is None:
            return None
        try:
            percent = round((self._position_device_units / self._range_max) * 100)
        except ZeroDivisionError:
            percent = 0
        return max(0, min(100, percent))

    async def ensure_connected(self) -> None:
        if self._bt_client and self._bt_client.is_connected:
            return
        # Serialize connects. Without this, a second command while the first is
        # still handshaking (e.g. re-dragging the position slider, or a status
        # poll overlapping a move) runs its own _connect(), whose teardown of the
        # "not yet connected" client kills the in-flight connection — so both
        # flap and time out. Queue them: the first connects, the rest reuse it.
        async with self._conn_lock:
            if self._bt_client and self._bt_client.is_connected:
                return
            await self._connect()

    def _link_is_fresh(self) -> bool:
        """True only if the link recently proved itself with a real exchange."""
        return (
            self._bt_client is not None
            and self._bt_client.is_connected
            and self._last_ok is not None
            and datetime.now() - self._last_ok < COMMAND_FRESH_WINDOW
        )

    async def ensure_fresh(self) -> None:
        """Prepare the link for an interactive command.

        Don't trust a cached "connected" socket: this blind sleeps its link
        silently, so the next write would just time out. Unless we've had a
        successful exchange in the last few seconds (e.g. mid-motion polling),
        reconnect now so the command lands on a live link instead of failing and
        making the user click again.
        """
        if self._link_is_fresh():
            return
        await self.refresh_status()

    async def _force_reconnect(self) -> None:
        """Tear down and rebuild the link (used to recover a failed command)."""
        async with self._conn_lock:
            await self.disconnect()
            await self._connect()

    async def _connect(self) -> None:
        if self._bt_client and self._bt_client.is_connected:
            return

        # A stale (disconnected) client may still hold an open proxy WebSocket.
        # Tear it down before making a new one, or sockets leak on every
        # reconnect after the blind drops its idle link.
        if self._bt_client is not None:
            await self.disconnect()

        ble_device = bluetooth.async_ble_device_from_address(
            self._hass, self._address, connectable=True
        )
        if ble_device:
            self._ble_device = ble_device
        if not self._ble_device:
            raise BleakError(f"Unable to find device with address {self._address}")

        LOGGER.debug("Connecting to Bliss blind %s", self._address)
        # establish_connection adds bleak-retry-connector's backoff/retries, which
        # matters for this weak-signal blind whose first reconnect after an idle
        # drop often needs a couple of tries.
        try:
            self._bt_client = await establish_connection(
                BleakClient,
                self._ble_device,
                self._address,
                disconnected_callback=self._disconnected_callback,
                max_attempts=4,
            )
            await self._bt_client.start_notify(
                RESPONSE_UUID, self._notification_handler
            )
            await self._initialize()
        except (BleakError, TimeoutError):
            await self.disconnect()
            raise
        self._available = True

    def _disconnected_callback(self, client: BleakClient) -> None:
        """Recover an unsolicited link drop without flapping HA state."""

        if client is not self._bt_client:
            return
        self._bt_client = None
        self._available = False
        if not self._closing and not (
            self._recovery_task and not self._recovery_task.done()
        ):
            self._recovery_task = self._hass.async_create_task(
                self._recover_connection()
            )

    async def _recover_connection(self) -> None:
        """Reconnect immediately after the blind ends its BLE session."""

        try:
            await self.refresh_status()
        except (BleakError, TimeoutError):
            LOGGER.debug("Background recovery failed for %s", self._address)

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

    async def close(self) -> None:
        """Stop background recovery and close the BLE client."""

        self._closing = True
        if self._recovery_task and not self._recovery_task.done():
            self._recovery_task.cancel()
            try:
                await self._recovery_task
            except asyncio.CancelledError:
                pass
        await self.disconnect()

    async def _initialize(self) -> None:
        await self._send_command(build_login(self._password), "login")
        await asyncio.sleep(0.1)
        await self._send_command(build_set_time(), "set_clock")

    async def _with_reconnect(self, action, label: str):
        """Run a BLE action and transparently rebuild a stale link.

        The blind sleeps its BLE connection when idle, so the first command after
        a gap can fail mid-handshake (login/notify/write). A single clean
        reconnect-and-retry turns that into a transparent success instead of a
        surfaced error.
        """
        for attempt in range(1, OPERATION_ATTEMPTS + 1):
            try:
                return await action()
            except (BleakError, TimeoutError) as err:
                if attempt == OPERATION_ATTEMPTS:
                    self._available = False
                    if self._status_callback:
                        self._status_callback(self._build_state())
                    raise
                LOGGER.debug(
                    "%s failed (%s/%s: %s); reconnecting",
                    label,
                    attempt,
                    OPERATION_ATTEMPTS,
                    err,
                )
                await self.disconnect()
                await asyncio.sleep(RETRY_DELAY)

    async def refresh_status(self) -> None:
        async def _do() -> None:
            await self.ensure_connected()
            self._status_event.clear()
            await self._send_command(READ_STATUS, "read_status")
            try:
                async with async_timeout.timeout(STATUS_RESPONSE_TIMEOUT):
                    await self._status_event.wait()
            except asyncio.TimeoutError:
                raise TimeoutError(f"Timeout waiting for status from {self._address}") from None

        async with self._status_lock:
            await self._with_reconnect(_do, "read_status")

    async def refresh_battery_status(self, *, force: bool = False) -> None:
        if (
            not force
            and self._battery_status is not None
            and self._last_battery_refresh is not None
            and datetime.now() - self._last_battery_refresh < BATTERY_REFRESH_INTERVAL
        ):
            return

        async def _do() -> None:
            await self.ensure_connected()
            self._battery_event.clear()
            await self._send_command(READ_BATTERY, "read_battery")
            try:
                async with async_timeout.timeout(BATTERY_RESPONSE_TIMEOUT):
                    await self._battery_event.wait()
            except asyncio.TimeoutError:
                raise TimeoutError(
                    f"Timeout waiting for battery status from {self._address}"
                ) from None

        await self._with_reconnect(_do, "read_battery")

    async def set_cover_fraction(self, fraction: float) -> None:
        fraction = max(0.0, min(1.0, fraction))
        device_position = round(self._range_max * fraction)
        await self._move_to_raw_position(device_position)

    async def _move_to_raw_position(self, device_position: int) -> None:
        device_position = max(0, min(0xFFFF, device_position))
        command = build_move_command(device_position)

        # Land on a fresh link first (reconnecting if the cached one is stale),
        # then send the GOTO exactly once — so a sleeping blind doesn't swallow
        # the command as a timeout, and a still-travelling blind doesn't get a
        # duplicate move that makes it jitter. If the send still fails, reconnect
        # and try once more rather than surfacing a timeout to the user.
        await self.ensure_fresh()
        try:
            await self._send_command(command, "move")
        except BleakError as err:
            LOGGER.debug("move failed (%s); reconnecting and retrying", err)
            await self._force_reconnect()
            await self._send_command(command, "move")

        self._position_device_units = device_position
        if self._status_callback:
            self._status_callback(self._build_state())

    async def set_cover_percentage(self, percentage: int) -> None:
        await self.set_cover_fraction(percentage / 100)

    async def open_cover(self) -> None:
        await self.set_cover_fraction(1.0)

    async def close_cover(self) -> None:
        await self.set_cover_fraction(0.0)

    async def stop_cover(self) -> None:
        try:
            await self.refresh_status()
        except BleakError:
            if self._position_device_units is None:
                raise
        if self._position_device_units is None:
            return
        await self._move_to_raw_position(self._position_device_units)

    def _build_state(self) -> dict:
        return {
            "available": self._available,
            "position": self.position_percentage,
            "raw_position": self._position_device_units,
            "battery_percentage": self._battery_percentage,
            "battery_voltage_mv": self._battery_voltage_mv,
            "battery_status": self._battery_status,
            "battery_status_code": self._battery_status_code,
            "battery_percentage_source": self._battery_percentage_source,
            "battery_raw_response": self._battery_raw_response,
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
            self._last_ok = datetime.now()
            await asyncio.sleep(0.1)

    def _apply_position(self, position: int) -> None:
        self._position_device_units = position
        if position > self._range_max:
            LOGGER.debug(
                "Expanding Bliss blind range for %s from %s to observed %s",
                self._address,
                self._range_max,
                position,
            )
            self._range_max = position
        if self._status_callback:
            self._status_callback(self._build_state())

    def _apply_battery_response(self, data: bytes) -> None:
        self._battery_raw_response = data.hex()
        self._last_battery_refresh = datetime.now()

        status_code, status, percentage = self._map_motor_return_to_battery(data[5])
        self._battery_status_code = status_code
        self._battery_status = status
        self._battery_percentage = percentage
        self._battery_percentage_source = BATTERY_PERCENTAGE_SOURCE
        self._battery_voltage_mv = None

        if self._status_callback:
            self._status_callback(self._build_state())
        self._battery_event.set()

    @staticmethod
    def _map_motor_return_to_battery(
        motor_return: int,
    ) -> tuple[int, str, int | None]:
        """Mirror the Bliss app's mapMotorRetToBattery(byte) logic."""
        battery_bits = motor_return & 0x18
        if battery_bits == 0x00:
            return BATTERY_FULL
        if battery_bits == 0x08:
            return BATTERY_LOW
        if battery_bits == 0x10:
            return BATTERY_EMPTY
        return BATTERY_UNKNOWN

    def _notification_handler(
        self, characteristic: BleakGATTCharacteristic, data: bytearray
    ) -> None:
        LOGGER.debug(
            "← notify %s (%s): %s",
            self._address,
            characteristic.uuid,
            data.hex(" "),
        )
        self._last_ok = datetime.now()
        self._process_notification_data(bytes(data))

    def _process_notification_data(self, data: bytes) -> None:
        if len(data) >= 8 and data[0] == 0xFF:
            command = data[4]
            if command in (0xD1, 0xD2) and len(data) > 5:
                self._apply_battery_response(data)
            if command == 0xD1 and len(data) >= 9:
                position = int.from_bytes(data[7:9], "little", signed=False)
                self._apply_position(position)
                self._status_event.set()
        # Some devices acknowledge movement on a different opcode
        if len(data) >= 8 and data[0] == 0xFF and data[4] == 0xBF:
            if len(data) >= 9:
                position = int.from_bytes(data[6:8], "little", signed=False)
                self._apply_position(position)

    @staticmethod
    async def async_discover(timeout: float = 10.0) -> list[tuple[str, str]]:
        """Discover nearby Bliss blinds using Bluetooth information from HA."""
        infos = bluetooth.async_discovered_service_info()
        matches: list[tuple[str, str]] = []
        for info in infos:
            name = info.name or ""
            if BLISS_NAME_PATTERN.match(name):
                matches.append((name, info.address))
        return matches
