from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from werkzeug.security import check_password_hash

from sqlalchemy import func
from src.models.db import Admin, User, UserActivity, db
from src.web.session import flash
from src.web.templates import render_template


router = APIRouter()


@router.get("/login", name="login")
async def login(request: Request):
    now = datetime.now(timezone.utc).timestamp()
    lockout_until = request.session.get("login_lockout_until", 0)
    lockout_remaining = int(lockout_until - now) if lockout_until and now < lockout_until else 0
    templates = request.app.state.templates
    return render_template(
        templates,
        request,
        "login.html",
        {
            "error": None,
            "lockout_remaining": lockout_remaining,
        },
    )


@router.post("/login", name="login_post")
async def login_post(
    request: Request,
    username: str = Form(default="admin"),
    password: str = Form(default=""),
):
    username = username.strip()
    now = datetime.now(timezone.utc).timestamp()
    lockout_until = request.session.get("login_lockout_until", 0)
    if lockout_until and now < lockout_until:
        flash(request, f"Too many failed attempts. Please wait {int(lockout_until - now)} seconds before trying again.", "error")
        return RedirectResponse(url="/login", status_code=302)

    def _authenticate():
        user = User.query.filter(func.lower(User.username) == username.lower()).first()
        if user and user.is_active and check_password_hash(user.password_hash, password):
            return user
        admin = Admin.query.filter(func.lower(Admin.username) == username.lower()).first()
        if admin and check_password_hash(admin.password_hash, password):
            return admin
        return None

    authenticated_user = await asyncio.to_thread(_authenticate)
    if authenticated_user:
        if hasattr(authenticated_user, "update_last_login"):
            authenticated_user.update_last_login()
        db.session.commit()
        if isinstance(authenticated_user, User):
            request.session["user_role"] = authenticated_user.role
            request.session["viewer_logged_in"] = authenticated_user.role == "viewer"
            request.session["admin_logged_in"] = authenticated_user.role == "admin"
            request.session["user_id"] = authenticated_user.id
            request.session["username"] = authenticated_user.username
            request.session["role"] = authenticated_user.role
            try:
                await asyncio.to_thread(
                    UserActivity.log_activity,
                    db.session,
                    authenticated_user.id,
                    authenticated_user.username,
                    "logged in",
                    "login",
                    request.client.host if request.client else None,
                    request.headers.get("User-Agent"),
                )
            except Exception:
                db.session.rollback()
        else:
            request.session["admin_logged_in"] = True
            request.session["viewer_logged_in"] = False
            request.session["user_role"] = "admin"
            request.session["role"] = "admin"
            request.session["user_id"] = authenticated_user.id
            request.session["username"] = authenticated_user.username

        request.app.state.system_locked = False
        request.session["locked"] = False
        for key in ("failed_login_attempts", "login_lockout_until", "login_lockout_count"):
            request.session.pop(key, None)
        return RedirectResponse(url="/viewer_dashboard" if request.session.get("user_role") == "viewer" else "/admin", status_code=302)

    failed_attempts = request.session.get("failed_login_attempts", 0) + 1
    request.session["failed_login_attempts"] = failed_attempts
    lockout_count = request.session.get("login_lockout_count", 0)
    if lockout_count == 0 and failed_attempts >= 5:
        request.session["login_lockout_until"] = now + 30
        request.session["login_lockout_count"] = 1
        request.session["failed_login_attempts"] = 0
        flash(request, "Too many failed attempts. Please wait 30 seconds before trying again.", "error")
    elif lockout_count > 0 and failed_attempts >= 2:
        request.session["login_lockout_until"] = now + 30
        request.session["login_lockout_count"] = lockout_count + 1
        request.session["failed_login_attempts"] = 0
        flash(request, "Too many failed attempts. Please wait 30 seconds before trying again.", "error")
    else:
        flash(request, "Invalid username or password. Please try again.", "error")
    return RedirectResponse(url="/login", status_code=302)


@router.get("/logout", name="logout")
async def logout(request: Request):
    user_id = request.session.get("user_id")
    username = request.session.get("username", "User")
    if user_id:
        try:
            await asyncio.to_thread(
                UserActivity.log_activity,
                db.session,
                user_id,
                username,
                "logged out",
                "logout",
                request.client.host if request.client else None,
                request.headers.get("User-Agent"),
            )
        except Exception:
            db.session.rollback()
    request.session.clear()
    request.app.state.system_locked = True
    flash(request, "You have been logged out successfully. System is now locked.", "info")
    return RedirectResponse(url="/locked", status_code=302)
