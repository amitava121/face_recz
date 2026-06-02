from __future__ import annotations

import asyncio

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from src.models.db import SystemSettings, db
from src.web.session import flash
from src.web.templates import render_template


router = APIRouter()


@router.get("/get_sleep_timer", name="get_sleep_timer")
async def get_sleep_timer():
    sleep_timer_seconds = await asyncio.to_thread(
        lambda: int(SystemSettings.get_setting("sleep_timer_seconds", 0))
    )
    return JSONResponse({"sleep_timer_seconds": sleep_timer_seconds})


@router.post("/set_sleep_timer", name="set_sleep_timer")
async def set_sleep_timer(
    request: Request,
    minutes: int = Form(default=0),
    seconds: int = Form(default=0),
):
    try:
        total_seconds = (minutes * 60) + seconds
        await asyncio.to_thread(SystemSettings.set_setting, "sleep_timer_seconds", total_seconds)
        flash(request, f"Sleep timer set to {minutes} minute(s) and {seconds} second(s).", "success")
    except Exception as exc:
        flash(request, f"Invalid input for sleep timer: {exc}", "error")
    return RedirectResponse(url="/admin", status_code=302)


@router.get("/sleep_mode", name="sleep_mode")
async def sleep_mode(request: Request):
    templates = request.app.state.templates
    return render_template(templates, request, "sleep_timer.html", {})


@router.get("/sleep", name="sleep")
async def sleep(request: Request):
    templates = request.app.state.templates
    return render_template(templates, request, "sleep_mode.html", {})


@router.get("/test_notifications", name="test_notifications")
async def test_notifications(request: Request):
    templates = request.app.state.templates
    return render_template(templates, request, "test_notifications.html", {})
