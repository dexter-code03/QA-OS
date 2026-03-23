"""WebSocket endpoint for streaming run events."""
from __future__ import annotations

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..events import event_bus
from ..helpers import extract_websocket_token, get_auth_token

router = APIRouter()


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
