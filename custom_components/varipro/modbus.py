"""Modbus TCP access for the VariPro room controller.

The only module that touches pymodbus, so changes in its API stay here.
Reads and writes share one connection and one lock, with at least
MIN_GAP seconds between requests (manual: polling delay >= 200 ms).
"""

from __future__ import annotations

import asyncio
import time

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

MIN_GAP = 0.2
DEFAULT_TIMEOUT = 5.0


class VariProModbus:
    """Async Modbus TCP bus; errors are returned as None/False, not raised."""

    def __init__(
        self,
        host: str,
        port: int,
        device_id: int,
        timeout: float = DEFAULT_TIMEOUT,
        min_gap: float = MIN_GAP,
    ) -> None:
        self._client = AsyncModbusTcpClient(host, port=port, timeout=timeout, retries=1)
        self._device_id = device_id
        self._min_gap = min_gap
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self.last_error: str | None = None

    async def _request(self, func, *args, **kwargs):
        async with self._lock:
            delay = self._last_request + self._min_gap - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                if not self._client.connected and not await self._client.connect():
                    self.last_error = "connection failed"
                    return None
                result = await func(*args, device_id=self._device_id, **kwargs)
            except (ModbusException, OSError, TimeoutError) as err:
                self.last_error = str(err) or type(err).__name__
                self._client.close()  # the next request reconnects
                return None
            finally:
                self._last_request = time.monotonic()
        if result.isError():
            self.last_error = str(result)
            return None
        self.last_error = None
        return result

    async def read(self, kind: str, start: int, count: int) -> list[int] | None:
        func = self._client.read_holding_registers if kind == "holding" else self._client.read_input_registers
        result = await self._request(func, start, count=count)
        return None if result is None else list(result.registers)

    async def write(self, addr: int, value: int) -> bool:
        return await self._request(self._client.write_register, addr, value) is not None

    def close(self) -> None:
        self._client.close()
