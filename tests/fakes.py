"""In-memory VariPro controller and snapshot store for tests (invented data only)."""

from __future__ import annotations

from typing import Any

from custom_components.varipro import device as vd


class FakeBus:
    """Behaves like VariProModbus against an in-memory controller."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self.holding = {0: vd.MODE_NORMAL, 1: 0, 2: 4, 3: 215, 4: 260, 201: 20}
        self.input = {i: 0 for i in range(3, 11)}
        self.input.update({3: 2, 6: 223, 7: 215, 102: 0, 115: 80, 116: 120})
        self.writes: list[tuple[int, int]] = []
        self.fail_writes: set[int] = set()
        self.offline = False
        self.last_error: str | None = None
        self.closed = False

    async def read(self, kind: str, start: int, count: int) -> list[int] | None:
        if self.offline:
            self.last_error = "timeout"
            return None
        regs = self.holding if kind == "holding" else self.input
        return [regs.get(a, 0) for a in range(start, start + count)]

    async def write(self, addr: int, value: int) -> bool:
        if self.offline or addr in self.fail_writes:
            self.last_error = "write failed"
            return False
        self.writes.append((addr, value))
        self.holding[addr] = value
        return True

    def close(self) -> None:
        self.closed = True


class FakeStore:
    def __init__(self) -> None:
        self.data: dict[str, Any] | None = None

    async def load(self) -> dict[str, Any] | None:
        return dict(self.data) if self.data else None

    async def save(self, snapshot: dict[str, Any]) -> None:
        self.data = dict(snapshot)

    async def clear(self) -> None:
        self.data = None


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now
