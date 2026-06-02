from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from werkzeug.security import check_password_hash

from src.models.db import Admin, User, db
from src.web.session import flash
from src.web.templates import render_template


router = APIRouter()


@router.get("/locked", name="locked")
async def locked(request: Request):
    lockout_until = request.session.get("unlock_lockout_until")
    now = datetime.now(timezone.utc).timestamp()
    lockout_remaining = 0
    if lockout_until and now < lockout_until:
        lockout_remaining = int(lockout_until - now)

    templates = request.app.state.templates
    return render_template(
        templates,
        request,
        "locked.html",
        {"lockout_remaining": lockout_remaining},
    )


@router.post("/lock", name="lock")
async def lock(request: Request):
    request.session["locked"] = True
    request.app.state.system_locked = True
    return RedirectResponse(url="/locked", status_code=302)


@router.post("/unlock", name="unlock")
async def unlock(
    request: Request,
    password: str = Form(default=""),
):
    admin = await asyncio.to_thread(lambda: Admin.query.filter_by(username="admin").first())
    now = datetime.now(timezone.utc).timestamp()
    lockout_until = request.session.get("unlock_lockout_until")
    lockout_count = request.session.get("unlock_lockout_count", 0)
    failed_attempts = request.session.get("failed_unlock_attempts", 0)
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if lockout_until and now < lockout_until:
        lockout_remaining = int(lockout_until - now)
        if is_ajax:
            return JSONResponse(
                {
                    "success": False,
                    "message": f"Too many failed attempts. Please wait {lockout_remaining} seconds.",
                    "lockout": True,
                    "lockout_remaining": lockout_remaining,
                },
                status_code=429,
            )
        flash(request, f"Too many failed attempts. Please wait {lockout_remaining} seconds.", "error")
        return RedirectResponse(url="/locked", status_code=302)

    if admin and check_password_hash(admin.password_hash, password):
        request.app.state.system_locked = False
        request.session["locked"] = False
        request.session["failed_unlock_attempts"] = 0
        request.session["unlock_lockout_until"] = 0
        request.session["unlock_lockout_count"] = 0
        if is_ajax:
            return JSONResponse(
                {"success": True, "message": "System unlocked!", "redirect": "/"},
                status_code=200,
            )
        flash(request, "System unlocked!", "success")
        return RedirectResponse(url="/", status_code=302)

    failed_attempts += 1
    if lockout_count == 0 and failed_attempts >= 5:
        request.session["unlock_lockout_until"] = now + 30
        request.session["unlock_lockout_count"] = 1
        request.session["failed_unlock_attempts"] = 0
        if is_ajax:
            return JSONResponse(
                {
                    "success": False,
                    "message": "Too many failed attempts. Please wait 30 seconds before trying again.",
                    "lockout": True,
                    "lockout_remaining": 30,
                },
                status_code=429,
            )
        flash(request, "Too many failed attempts. Please wait 30 seconds before trying again.", "error")
    elif lockout_count > 0 and failed_attempts >= 2:
        request.session["unlock_lockout_until"] = now + 30
        request.session["unlock_lockout_count"] = lockout_count + 1
        request.session["failed_unlock_attempts"] = 0
        if is_ajax:
            return JSONResponse(
                {
                    "success": False,
                    "message": "Too many failed attempts. Please wait 30 seconds before trying again.",
                    "lockout": True,
                    "lockout_remaining": 30,
                },
                status_code=429,
            )
        flash(request, "Too many failed attempts. Please wait 30 seconds before trying again.", "error")
    else:
        request.session["failed_unlock_attempts"] = failed_attempts
        if is_ajax:
            return JSONResponse(
                {"success": False, "message": "Incorrect password. Try again.", "lockout": False},
                status_code=401,
            )
        flash(request, "Incorrect password. Try again.", "error")
    return RedirectResponse(url="/locked", status_code=302)


@router.post("/unlock_viewer", name="unlock_viewer")
async def unlock_viewer(
    request: Request,
    username: str = Form(default=""),
    password: str = Form(default=""),
):
    username = username.strip()
    password = password.strip()
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if not username or not password:
        if is_ajax:
            return JSONResponse(
                {"success": False, "message": "Please enter both Student ID and password.", "lockout": False},
                status_code=400,
            )
        flash(request, "Please enter both Student ID and password.", "error")
        return RedirectResponse(url="/locked", status_code=302)

    now = datetime.now(timezone.utc).timestamp()
    lockout_until = request.session.get("viewer_unlock_lockout_until")
    lockout_count = request.session.get("viewer_unlock_lockout_count", 0)
    failed_attempts = request.session.get("failed_viewer_unlock_attempts", 0)

    if lockout_until and now < lockout_until:
        lockout_remaining = int(lockout_until - now)
        if is_ajax:
            return JSONResponse(
                {
                    "success": False,
                    "message": f"Too many failed attempts. Please wait {lockout_remaining} seconds.",
                    "lockout": True,
                    "lockout_remaining": lockout_remaining,
                },
                status_code=429,
            )
        flash(request, f"Too many failed attempts. Please wait {lockout_remaining} seconds.", "error")
        return RedirectResponse(url="/locked", status_code=302)

    user = await asyncio.to_thread(lambda: User.query.filter_by(username=username, role="viewer").first())
    if user and check_password_hash(user.password_hash, password):
        if not user.is_active:
            if is_ajax:
                return JSONResponse(
                    {
                        "success": False,
                        "message": "Your account has been deactivated. Please contact an administrator.",
                        "lockout": False,
                    },
                    status_code=403,
                )
            flash(request, "Your account has been deactivated. Please contact an administrator.", "error")
            return RedirectResponse(url="/locked", status_code=302)

        request.app.state.system_locked = False
        request.session["locked"] = False
        request.session["_user_id"] = str(user.id)
        request.session["_remember"] = True
        request.session["admin_logged_in"] = False
        request.session["viewer_logged_in"] = True
        request.session["user_id"] = user.id
        request.session["username"] = user.username
        request.session["role"] = user.role
        request.session["user_role"] = user.role
        request.session["failed_viewer_unlock_attempts"] = 0
        request.session["viewer_unlock_lockout_until"] = 0
        request.session["viewer_unlock_lockout_count"] = 0

        def _update_last_login() -> None:
            user.update_last_login()
            db.session.commit()

        await asyncio.to_thread(_update_last_login)

        if is_ajax:
            return JSONResponse(
                {"success": True, "message": f"Welcome back, {user.username}!", "redirect": "/viewer_dashboard"},
                status_code=200,
            )
        flash(request, f"Welcome back, {user.username}!", "success")
        return RedirectResponse(url="/viewer_dashboard", status_code=302)

    failed_attempts += 1
    if lockout_count == 0 and failed_attempts >= 5:
        request.session["viewer_unlock_lockout_until"] = now + 30
        request.session["viewer_unlock_lockout_count"] = 1
        request.session["failed_viewer_unlock_attempts"] = 0
        message = "Too many failed attempts. Please wait 30 seconds before trying again."
        status_code = 429
        payload = {"success": False, "message": message, "lockout": True, "lockout_remaining": 30}
    elif lockout_count > 0 and failed_attempts >= 2:
        request.session["viewer_unlock_lockout_until"] = now + 30
        request.session["viewer_unlock_lockout_count"] = lockout_count + 1
        request.session["failed_viewer_unlock_attempts"] = 0
        message = "Too many failed attempts. Please wait 30 seconds before trying again."
        status_code = 429
        payload = {"success": False, "message": message, "lockout": True, "lockout_remaining": 30}
    else:
        request.session["failed_viewer_unlock_attempts"] = failed_attempts
        message = "Invalid Student ID or password. Please try again."
        status_code = 401
        payload = {"success": False, "message": message, "lockout": False}

    if is_ajax:
        return JSONResponse(payload, status_code=status_code)

    flash(request, message, "error")
    return RedirectResponse(url="/locked", status_code=302)


@router.get("/reset_password", name="reset_password")
async def reset_password_redirect(request: Request):
    flash(request, "Reset password migration is in progress.", "info")
    return RedirectResponse(url="/locked", status_code=302)
