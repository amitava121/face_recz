from __future__ import annotations

import asyncio

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from src.services.webrtc_server import process_offer_payload, release_active_connections
from src.web.realtime import emit_attendance_marked
from src.web.services.attendance import (
    decode_image_bytes,
    mark_attendance_for_student,
    process_attendance_image,
    process_registration_image,
    register_face_embedding,
)
from src.web.templates import render_template


router = APIRouter()


class AttendanceRequest(BaseModel):
    student_id: int
    confidence: float = 0.0
    device_id: str | None = None
    name: str | None = None


class RegisterFaceRequest(BaseModel):
    student_id: int
    embedding: list[float]


@router.get("/attendance", name="attendance")
async def attendance(request: Request):
    if request.app.state.system_locked:
        return RedirectResponse(url="/locked", status_code=302)
    templates = request.app.state.templates
    return render_template(templates, request, "attendance.html", {})


@router.api_route("/start_attendance", methods=["GET", "POST"], name="start_attendance")
async def start_attendance(request: Request):
    request.app.state.attendance_running = True
    return JSONResponse({"success": True, "attendance_running": True})


@router.api_route("/stop_attendance", methods=["GET", "POST"], name="stop_attendance")
async def stop_attendance(request: Request):
    request.app.state.attendance_running = False
    return JSONResponse({"status": "success", "message": "Attendance stopped"})


@router.post("/webrtc/offer", name="webrtc_offer")
async def webrtc_offer(request: Request):
    payload = await request.json()
    body, status_code = await process_offer_payload(payload)
    return JSONResponse(body, status_code=status_code)


@router.post("/release_webrtc", name="release_webrtc")
async def release_webrtc(request: Request):
    payload = await request.json()
    body, status_code = await release_active_connections(payload)
    return JSONResponse(body, status_code=status_code)


@router.post("/process_image", name="process_image")
async def process_image(
    request: Request,
    image: UploadFile = File(...),
    mode: str = Form(default="attendance"),
    student_id: str = Form(default=""),
):
    image_data = await image.read()
    img = await asyncio.to_thread(decode_image_bytes, image_data)
    if img is None:
        return JSONResponse({"error": "Could not decode image"}, status_code=400)

    parsed_student_id = int(student_id) if student_id.isdigit() else None
    if mode == "registration":
        if not parsed_student_id:
            return JSONResponse({"error": "Student ID is required"}, status_code=400)
        body, status_code = await asyncio.to_thread(
            process_registration_image,
            img,
            parsed_student_id,
            request.app.state.registration_complete,
        )
        return JSONResponse(body, status_code=status_code)

    body, status_code = await asyncio.to_thread(
        process_attendance_image,
        img,
        bool(request.app.state.attendance_running),
        request.app.state.active_liveness_challenges,
    )
    return JSONResponse(body, status_code=status_code)


@router.post("/release_camera", name="release_camera")
async def release_camera(request: Request):
    data = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    student_id = data.get("student_id")
    mode = data.get("mode")
    if mode == "registration" and student_id:
        if isinstance(student_id, str) and student_id.isdigit():
            student_id = int(student_id)
        request.app.state.registration_complete[student_id] = True
        if request.session.get("registering_student_id") == student_id:
            request.session.pop("registering_student_id", None)
    return JSONResponse({"success": True})


@router.post("/mark_attendance", name="mark_attendance_api")
async def mark_attendance_api(payload: AttendanceRequest):
    body, status_code = await asyncio.to_thread(
        mark_attendance_for_student,
        payload.student_id,
        payload.confidence,
        payload.device_id,
    )
    if body.get("success"):
        await emit_attendance_marked(
            {
                "student_id": payload.student_id,
                "name": body["name"],
                "timestamp": body.get("timestamp"),
            }
        )
    return JSONResponse(body, status_code=status_code)


@router.post("/api/mark_attendance", name="api_mark_attendance")
async def api_mark_attendance(request: Request, payload: AttendanceRequest):
    if not request.app.state.attendance_running:
        return JSONResponse({"error": "Attendance system is not active"}, status_code=400)
    return await mark_attendance_api(payload)


@router.post("/api/register_face", name="api_register_face")
async def api_register_face(request: Request, payload: RegisterFaceRequest):
    body, status_code = await asyncio.to_thread(
        register_face_embedding,
        payload.student_id,
        payload.embedding,
        request.app.state.registration_complete,
    )
    return JSONResponse(body, status_code=status_code)


@router.get("/webrtc_test", name="webrtc_test")
async def webrtc_test(request: Request):
    templates = request.app.state.templates
    return render_template(templates, request, "webrtc_test.html", {}, status_code=200)
