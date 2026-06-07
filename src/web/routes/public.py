from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from src.models.db import Attendance, Student, db
from src.web.templates import render_template
from src.web.timezone_utils import local_day_bounds_from_string, now_local


router = APIRouter()


# ── Feature flag ────────────────────────────────────────
# Set to False to revert to the old homepage instantly.
ENABLE_DASHBOARD = True


def _load_dashboard_stats():
    """Load all stats for the dashboard in a single thread-safe call."""
    total_students = Student.query.count()

    # Today's attendance count
    today_str = now_local().date().isoformat()
    start_utc, end_utc = local_day_bounds_from_string(today_str)
    today_attendance = (
        Attendance.query
        .filter(Attendance.timestamp >= start_utc, Attendance.timestamp <= end_utc)
        .count()
    )

    # Attendance rate
    attendance_rate = 0
    if total_students > 0:
        attendance_rate = round((today_attendance / total_students) * 100, 1)
    attendance_rate = min(attendance_rate, 100)

    # Recent records for activity feed
    recent_raw = (
        Attendance.query
        .filter(Attendance.timestamp >= start_utc, Attendance.timestamp <= end_utc)
        .order_by(Attendance.timestamp.desc())
        .limit(10)
        .all()
    )
    recent_records = []
    for record in recent_raw:
        student = record.student
        recent_records.append({
            "student_name": student.name if student else (record.student_name or "Unknown"),
            "student_code": student.student_code if student else (record.student_code or "N/A"),
            "student_department": student.department if student else (record.student_department or "N/A"),
            "time": record.timestamp.strftime("%H:%M:%S") if record.timestamp else "N/A",
        })

    # Weekly trend (last 7 days)
    weekly_labels = []
    weekly_data = []
    now = datetime.now(timezone.utc)
    for i in range(6, -1, -1):
        day = now - timedelta(days=i)
        day_str = day.strftime("%Y-%m-%d")
        try:
            d_start, d_end = local_day_bounds_from_string(day_str)
            count = (
                Attendance.query
                .filter(Attendance.timestamp >= d_start, Attendance.timestamp <= d_end)
                .count()
            )
        except Exception:
            count = 0
        weekly_labels.append(day.strftime("%a"))
        weekly_data.append(count)

    # Department distribution (total students per dept)
    dept_labels = []
    dept_data = []
    try:
        dept_rows = (
            db.session.query(Student.department, db.func.count(Student.id))
            .filter(Student.department.isnot(None))
            .group_by(Student.department)
            .order_by(db.func.count(Student.id).desc())
            .limit(8)
            .all()
        )
        for dept_name, dept_count in dept_rows:
            if dept_name:
                dept_labels.append(dept_name)
                dept_data.append(dept_count)
    except Exception:
        pass

    # Top department today
    top_department = None
    try:
        top_row = (
            db.session.query(Student.department, db.func.count(Attendance.id))
            .join(Student, Attendance.student_id == Student.id)
            .filter(Attendance.timestamp >= start_utc, Attendance.timestamp <= end_utc)
            .filter(Student.department.isnot(None))
            .group_by(Student.department)
            .order_by(db.func.count(Attendance.id).desc())
            .first()
        )
        if top_row:
            top_department = top_row[0]
    except Exception:
        pass

    return {
        "total_students": total_students,
        "today_attendance": today_attendance,
        "attendance_rate": attendance_rate,
        "recent_records": recent_records,
        "weekly_labels": weekly_labels,
        "weekly_data": weekly_data,
        "dept_labels": dept_labels,
        "dept_data": dept_data,
        "top_department": top_department,
    }


@router.get("/", name="index")
async def index(request: Request):
    if request.app.state.system_locked:
        return RedirectResponse(url="/locked", status_code=302)

    if request.session.get("viewer_logged_in"):
        return RedirectResponse(url="/viewer_dashboard", status_code=302)

    if not ENABLE_DASHBOARD:
        # Fallback to old homepage
        templates = request.app.state.templates
        return render_template(
            templates,
            request,
            "all_templates.html",
            {"show_attendance": False, "show_face_capture": False},
        )

    stats = await asyncio.to_thread(_load_dashboard_stats)
    templates = request.app.state.templates
    return render_template(templates, request, "dashboard.html", stats)


@router.get("/api/dashboard_stats", name="api_dashboard_stats")
async def api_dashboard_stats(request: Request):
    """Lightweight JSON endpoint for auto-refresh."""
    try:
        stats = await asyncio.to_thread(_load_dashboard_stats)
        return JSONResponse({
            "total_students": stats["total_students"],
            "today_attendance": stats["today_attendance"],
            "attendance_rate": stats["attendance_rate"],
        })
    except Exception:
        return JSONResponse({"error": "Failed to load stats"}, status_code=500)
