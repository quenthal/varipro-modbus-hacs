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


async def test_gateway_restart_leaves_one_connection() -> None:
    """After the gateway restarts, only our own reconnect may open a connection.

    pymodbus' background reconnect used to open a second one alongside it,
    which a gateway accepting only a few connections may not tolerate.
    """
    port = _free_port()

    async def start():
        device = SimDevice(id=1, simdata=[SimData(0, values=[2] * 10, datatype=DataType.REGISTERS)])
        srv = ModbusTcpServer(device, address=("127.0.0.1", port))
        task = asyncio.create_task(srv.serve_forever())
        await asyncio.sleep(0.2)
        return srv, task

    srv, task = await start()
    bus = VariProModbus("127.0.0.1", port, 1, timeout=1)
    try:
        assert await bus.read("holding", 0, 1) == [2]
        await srv.shutdown()
        task.cancel()
        await asyncio.sleep(0.2)
        assert await bus.read("holding", 0, 1) is None  # gateway away

        srv, task = await start()
        assert await bus.read("holding", 0, 1) == [2]  # recovers on the next poll
        await asyncio.sleep(1.0)  # a background reconnect would have fired by now
        assert len(srv.active_connections) == 1
    finally:
        bus.close()
        await srv.shutdown()
        task.cancel()


async def test_missing_responses_keep_connection_until_pymodbus_drops_it() -> None:
    """A gateway that stops answering (e.g. WiFi drop) keeps the same TCP
    connection for a few requests instead of a new one per timeout."""
    accepted = 0

    async def silent(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal accepted
        accepted += 1
        while await reader.read(256):
            pass  # never answer
        writer.close()

    server = await asyncio.start_server(silent, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    bus = VariProModbus("127.0.0.1", port, 1, timeout=0.2, min_gap=0)
    try:
        for _ in range(3):
            assert await bus.read("holding", 0, 1) is None
        assert accepted == 1  # same connection reused across timeouts
        for _ in range(4):
            await bus.read("holding", 0, 1)
        assert accepted == 2  # pymodbus gave up on the silent connection once
    finally:
        bus.close()
        server.close()
        await server.wait_closed()


async def test_gateway_closing_at_once_fails_fast() -> None:
    """A gateway that accepts and immediately closes (slots full) is reported
    right away instead of waiting for the response timeout."""

    async def reject(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()

    server = await asyncio.start_server(reject, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    bus = VariProModbus("127.0.0.1", port, 1, timeout=3, min_gap=0)
    try:
        loop = asyncio.get_running_loop()
        start = loop.time()
        assert await bus.read("holding", 0, 1) is None
        assert loop.time() - start < 1.5
        assert "closed by the gateway" in bus.last_error
    finally:
        bus.close()
        server.close()
        await server.wait_closed()
