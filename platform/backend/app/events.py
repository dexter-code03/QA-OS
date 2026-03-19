from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RunEvent:
    run_id: int
    type: str
    payload: dict[str, Any]


class RunEventBus:
    def __init__(self) -> None:
        self._subscribers: dict[int, set[asyncio.Queue[RunEvent]]] = {}

    async def publish(self, event: RunEvent) -> None:
        queues = list(self._subscribers.get(event.run_id, set()))
        for q in queues:
            await q.put(event)

    def subscribe(self, run_id: int) -> asyncio.Queue[RunEvent]:
        q: asyncio.Queue[RunEvent] = asyncio.Queue()
        self._subscribers.setdefault(run_id, set()).add(q)
        return q

    def unsubscribe(self, run_id: int, q: asyncio.Queue[RunEvent]) -> None:
        subs = self._subscribers.get(run_id)
        if not subs:
            return
        subs.discard(q)
        if not subs:
            self._subscribers.pop(run_id, None)


event_bus = RunEventBus()

