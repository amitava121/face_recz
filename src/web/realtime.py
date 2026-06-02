from __future__ import annotations

import socketio


server = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")
app = socketio.ASGIApp(server)


@server.event
async def connect(sid, environ, auth):
    return True


@server.event
async def disconnect(sid):
    return None


async def emit_attendance_marked(payload: dict[str, object]) -> None:
    await server.emit("attendance_marked", payload)
