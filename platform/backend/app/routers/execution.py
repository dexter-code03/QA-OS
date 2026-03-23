"""WebSocket endpoint for streaming run events."""
from __future__ import annotations

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..events import event_bus
from ..helpers import extract_websocket_token, get_auth_token

router = APIRouter()


@router.get("/api/runs/{run_id}/events")
def get_run_events(run_id: int, after: int = 0):
    """Return events for this run after the given sequence number (reconnection recovery).

    The in-memory event bus does not persist history; this endpoint is reserved for
    when a durable event log exists. Until then, returns an empty list.
    """
    _ = run_id
    return {"events": [], "after": after}


@router.websocket("/ws/runs/{run_id}")
async def ws_run_events(websocket: WebSocket, run_id: int):
    if extract_websocket_token(websocket) != get_auth_token():
        await websocket.close(code=1008)
        return
    await websocket.accept()
    q = event_bus.subscribe(run_id)
    try:
        while True:
            event = await q.get()
            await websocket.send_text(json.dumps({"type": event.type, "payload": event.payload}))
    except WebSocketDisconnect:
        pass
    finally:
        event_bus.unsubscribe(run_id, q)
