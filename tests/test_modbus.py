"""VariProModbus against an in-process pymodbus TCP server (invented register values)."""

import asyncio
import socket

from pymodbus.server import ModbusTcpServer
from pymodbus.simulator import DataType, SimData, SimDevice
import pytest

from custom_components.varipro.device import REG_MODE, REG_TARGET_HOME
from custom_components.varipro.modbus import VariProModbus

pytestmark = pytest.mark.usefixtures("socket_enabled")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def server():
    # One shared register block: holding and input registers use the same addresses
    values = [0] * 300
    values[0], values[3], values[6] = 2, 215, 223
    device = SimDevice(id=1, simdata=[SimData(0, values=values, datatype=DataType.REGISTERS)])
    port = _free_port()
    srv = ModbusTcpServer(device, address=("127.0.0.1", port))
    task = asyncio.create_task(srv.serve_forever())
    await asyncio.sleep(0.2)
    yield port
    await srv.shutdown()
    task.cancel()


async def test_read_and_write(server) -> None:
    bus = VariProModbus("127.0.0.1", server, 1, timeout=2)
    try:
        assert await bus.read("holding", 0, 5) == [2, 0, 0, 215, 0]
        assert (await bus.read("input", 3, 8))[3] == 223
        assert await bus.write(REG_TARGET_HOME, 225)
        assert await bus.read("holding", REG_TARGET_HOME, 1) == [225]
        assert await bus.write(REG_MODE, 3)
        assert bus.last_error is None
    finally:
        bus.close()


async def test_unreachable_returns_none() -> None:
    bus = VariProModbus("127.0.0.1", _free_port(), 1, timeout=1)
    try:
        assert await bus.read("holding", 0, 1) is None
        assert not await bus.write(REG_MODE, 2)
        assert bus.last_error
    finally:
        bus.close()


async def test_requests_are_spaced(server) -> None:
    bus = VariProModbus("127.0.0.1", server, 1, timeout=2, min_gap=0.2)
    try:
        loop = asyncio.get_running_loop()
        start = loop.time()
        await asyncio.gather(*(bus.read("holding", 0, 1) for _ in range(4)))
        assert loop.time() - start >= 0.6  # 3 gaps of 200 ms
    finally:
        bus.close()
