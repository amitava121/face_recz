from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from werkzeug.security import check_password_hash, generate_password_hash

from src.models.db import Attendance, User, db
from src.web.session import flash
from src.web.templates import render_template


router = APIRouter()


def _viewer_only(request: Request):
    if not request.session.get("viewer_logged_in"):
        return RedirectResponse(url="/login", status_code=302)
    return None


@router.get("/viewer_dashboard", name="viewer_dashboard")
async def viewer_dashboard(request: Request):
    guard = _viewer_only(request)
    if guard:
        return guard

    def _load():
        user = User.query.get(request.session.get("user_id"))
        student = user.student if user else None
        records = []
        total_days = 0
        attendance_this_month = 0
        if student:
            records = Attendance.query.filter_by(student_id=student.id).order_by(Attendance.timestamp.desc()).limit(100).all()
            total_days = Attendance.query.filter_by(student_id=student.id).count()
            now = datetime.now(timezone.utc)
            first_day = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            attendance_this_month = Attendance.query.filter(
                Attendance.student_id == student.id,
                Attendance.timestamp >= first_day,
            ).count()
        return user, student, records, total_days, attendance_this_month

    user, student, records, total_days, attendance_this_month = await asyncio.to_thread(_load)
    templates = request.app.state.templates
    return render_template(
        templates,
        request,
        "viewer_dashboard.html",
        {
            "student": student,
            "user": user,
            "attendance_records": records,
            "total_days": total_days,
            "attendance_this_month": attendance_this_month,
            "user_role": request.session.get("user_role", "viewer"),
        },
    )


@router.api_route("/viewer_profile", methods=["GET", "POST"], name="viewer_profile")
async def viewer_profile(
    request: Request,
    action: str = Form(default="update_profile"),
    student_code: str = Form(default=""),
    name: str = Form(default=""),
    department: str = Form(default=""),
    phone_number: str = Form(default=""),
    email: str = Form(default=""),
    old_password: str = Form(default=""),
    new_password: str = Form(default=""),
    confirm_password: str = Form(default=""),
):
    guard = _viewer_only(request)
    if guard:
        return guard

    user = await asyncio.to_thread(lambda: User.query.get(request.session.get("user_id")))
    if not user or not user.student:
        flash(request, "No student record linked to your account", "error")
        return RedirectResponse(url="/viewer_dashboard", status_code=302)

    student = user.student
    if request.method == "POST":
        try:
            if action == "update_profile":
                student.student_code = student_code.strip() or student.student_code
                student.name = name.strip() or student.name
                student.department = department.strip() or student.department
                student.phone_number = phone_number.strip()
                student.updated_at = datetime.now(timezone.utc)
                if email.strip():
                    user.email = email.strip()
                user.username = student.student_code
                request.session["username"] = user.username
                db.session.commit()
                flash(request, "Profile updated successfully!", "success")
            elif action == "change_password":
                if not check_password_hash(user.password_hash, old_password):
                    flash(request, "Current password is incorrect", "error")
                    return RedirectResponse(url="/viewer_profile", status_code=302)
                if len(new_password) < 6 or new_password != confirm_password:
                    flash(request, "New passwords do not match or are too short", "error")
                    return RedirectResponse(url="/viewer_profile", status_code=302)
                user.password_hash = generate_password_hash(new_password)
                db.session.commit()
                request.session.clear()
                request.app.state.system_locked = True
                flash(request, "Password changed successfully! Please login with your new password.", "success")
                return RedirectResponse(url="/locked", status_code=302)
        except Exception as exc:
            db.session.rollback()
            flash(request, f"Error updating profile: {exc}", "error")
        return RedirectResponse(url="/viewer_profile", status_code=302)

    templates = request.app.state.templates
    return render_template(templates, request, "viewer_profile.html", {"student": student, "user": user})
