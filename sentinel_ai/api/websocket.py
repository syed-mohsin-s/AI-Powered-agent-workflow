"""
Sentinel-AI WebSocket Handler.

Real-time updates for the monitoring dashboard.
"""

import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from sentinel_ai.core.event_bus import get_event_bus
from sentinel_ai.utils.logger import get_logger
from sentinel_ai.utils.metrics import get_metrics

router = APIRouter(tags=["WebSocket"])
logger = get_logger("api.websocket")

# Connected WebSocket clients
_ws_clients: set[WebSocket] = set()


async def broadcast_event(event_data: dict):
    """Broadcast an event to all connected WebSocket clients."""
    if not _ws_clients:
        return
    
    message = json.dumps(event_data, default=str)
    disconnected = set()
    
    for ws in _ws_clients:
        try:
            await ws.send_text(message)
        except Exception:
            disconnected.add(ws)
    
    _ws_clients -= disconnected


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time dashboard updates."""
    await websocket.accept()
    _ws_clients.add(websocket)
    
    # Register broadcast callback with event bus
    event_bus = get_event_bus()
    event_bus.register_ws_callback(broadcast_event)
    
    logger.info(f"WebSocket client connected (total: {len(_ws_clients)})")
    
    try:
        # Send initial state
        metrics = get_metrics()
        await websocket.send_json({
            "event_type": "initial_state",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": metrics.get_dashboard_snapshot(),
        })
        
        # Start periodic metrics push
        metrics_task = asyncio.create_task(_push_metrics(websocket))
        
        # Keep connection alive and handle incoming messages
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=60)
                # Handle client messages (e.g., filter subscriptions)
                request = json.loads(data)
                if request.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
            except asyncio.TimeoutError:
                # Send heartbeat
                await websocket.send_json({
                    "event_type": "heartbeat",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        _ws_clients.discard(websocket)
        logger.info(f"WebSocket client disconnected (remaining: {len(_ws_clients)})")


async def _push_metrics(websocket: WebSocket):
    """Push metrics to a specific client every 5 seconds."""
    metrics = get_metrics()
    while True:
        try:
            await asyncio.sleep(5)
            await websocket.send_json({
                "event_type": "metrics_update",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": metrics.get_dashboard_snapshot(),
            })
        except Exception:
            break
