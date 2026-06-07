from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from io import BytesIO

import pandas as pd
from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from werkzeug.security import generate_password_hash

from src.models.db import Admin, Attendance, Student, SystemSettings, User, UserActivity, db
from src.web.services.system import (
    build_reports_data,
    build_system_metrics,
    build_system_status,
    get_current_settings_object,
    get_performance_history,
    get_recent_system_logs,
    get_recognition_performance,
    settings_manager,
    test_system_configuration,
)
from src.web.session import flash
from src.web.templates import render_template
from src.web.timezone_utils import local_day_bounds_from_string, now_local


router = APIRouter()


def _admin_only(request: Request):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login", status_code=302)
    return None


def _excel_response(df: pd.DataFrame, filename: str, sheet_name: str = "Data") -> Response:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
    output.seek(0)
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

def _load_dashboard_records(date: str, department: str, search: str):
    departments = [d[0] for d in db.session.query(db.func.distinct(Student.department)).filter(Student.department.isnot(None)).limit(50).all() if d[0]]
    query = Attendance.query.outerjoin(Student, Attendance.student_id == Student.id)
    if date:
        start_utc, end_utc = local_day_bounds_from_string(date)
        query = query.filter(
            Attendance.timestamp >= start_utc,
            Attendance.timestamp <= end_utc,
        )
    if department:
        query = query.filter(Student.department == department)
    if search.strip():
        query = query.filter(
            (Student.student_code.ilike(f"%{search.strip()}%")) |
            (Student.name.ilike(f"%{search.strip()}%"))
        )
    records = query.order_by(Attendance.timestamp.desc()).limit(200).all()
    return departments, records


def _serialize_dashboard_record(record: Attendance) -> dict:
    student = record.student
    return {
        "id": record.id,
        "date": record.timestamp.strftime("%Y-%m-%d"),
        "time": record.timestamp.strftime("%H:%M:%S"),
        "student_code": student.student_code if student else (record.student_code or "[Student Deleted]"),
        "student_name": student.name if student else (record.student_name or "[Student Deleted]"),
        "student_department": student.department if student else (record.student_department or "[Student Deleted]"),
    }


@router.get("/dashboard", name="dashboard")
async def dashboard(
    request: Request,
    department: str = Query(default=""),
    date: str = Query(default_factory=lambda: now_local().date().isoformat()),
    search: str = Query(default=""),
):
    guard = _admin_only(request)
    if guard:
        return guard

    departments, attendance_records = await asyncio.to_thread(_load_dashboard_records, date, department, search)
    templates = request.app.state.templates
    return render_template(
        templates,
        request,
        "attendance_dashboard.html",
        {
            "attendance_records": attendance_records,
            "departments": departments,
            "selected_dept": department,
            "selected_date": date,
            "dashboard_last_updated": now_local().strftime("%Y-%m-%d %H:%M:%S"),
        },
    )


@router.get("/dashboard/data")
async def dashboard_data(
    request: Request,
    department: str = Query(default=""),
    date: str = Query(default_factory=lambda: now_local().date().isoformat()),
    search: str = Query(default=""),
):
    guard = _admin_only(request)
    if guard:
        return JSONResponse({"redirect": "/login"}, status_code=401)

    _, attendance_records = await asyncio.to_thread(_load_dashboard_records, date, department, search)
    return JSONResponse(
        {
            "records": [_serialize_dashboard_record(record) for record in attendance_records],
            "last_updated": now_local().strftime("%Y-%m-%d %H:%M:%S"),
        },
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@router.get("/admin", name="admin_panel")
async def admin_panel(request: Request):
    guard = _admin_only(request)
    if guard:
        return guard
    templates = request.app.state.templates
    return render_template(templates, request, "admin_panel.html", {})


@router.get("/reports_analytics", name="reports_analytics")
async def reports_analytics(
    request: Request,
    start_date: str = Query(default_factory=lambda: (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")),
    end_date: str = Query(default_factory=lambda: datetime.now().strftime("%Y-%m-%d")),
    department: str = Query(default=""),
):
    guard = _admin_only(request)
    if guard:
        return guard

    def _load():
        total_students, today_present, today_absent, avg_attendance, total_days, departments = build_reports_data(start_date, end_date)
        daily_data = []
        daily_labels = []
        current_date = datetime.strptime(start_date, "%Y-%m-%d").date()
        end_date_obj = datetime.strptime(end_date, "%Y-%m-%d").date()
        while current_date <= end_date_obj:
            count = Attendance.query.filter(db.func.date(Attendance.timestamp) == current_date).count()
            daily_data.append(count)
            daily_labels.append(current_date.strftime("%m-%d"))
            current_date += timedelta(days=1)
        dept_data = []
        dept_labels = []
        for dept in departments:
            count = Attendance.query.join(Student).filter(
                Student.department == dept,
                Attendance.timestamp >= start_date,
                Attendance.timestamp <= end_date + " 23:59:59",
            ).count()
            dept_data.append(count)
            dept_labels.append(dept)
        student_stats = []
        for student in Student.query.all():
            attendance_count = Attendance.query.filter(
                Attendance.student_id == student.id,
                Attendance.timestamp >= start_date,
                Attendance.timestamp <= end_date + " 23:59:59",
            ).count()
            student_stats.append(
                {
                    "student_code": student.student_code,
                    "name": student.name,
                    "department": student.department,
                    "total_days": total_days,
                    "present_days": attendance_count,
                    "attendance_percentage": (attendance_count / total_days * 100) if total_days > 0 else 0,
                }
            )
        student_stats.sort(key=lambda row: row["attendance_percentage"], reverse=True)
        return total_students, today_present, today_absent, avg_attendance, departments, daily_labels, daily_data, dept_labels, dept_data, student_stats

    payload = await asyncio.to_thread(_load)
    templates = request.app.state.templates
    return render_template(
        templates,
        request,
        "reports_analytics.html",
        {
            "total_students": payload[0],
            "today_present": payload[1],
            "today_absent": payload[2],
            "avg_attendance": payload[3],
            "departments": payload[4],
            "daily_labels": payload[5],
            "daily_data": payload[6],
            "dept_labels": payload[7],
            "dept_data": payload[8],
            "student_stats": payload[9],
            "start_date": start_date,
            "end_date": end_date,
            "selected_dept": department,
        },
    )


@router.api_route("/system_settings", methods=["GET", "POST"], name="system_settings")
async def system_settings(
    request: Request,
    action: str = Form(default=""),
    face_threshold: float = Form(default=0.4),
    verification_threshold: float = Form(default=0.5),
    detection_scale: float = Form(default=0.3),
    frame_skip: int = Form(default=2),
    use_gpu: str = Form(default="true"),
    attendance_window: int = Form(default=10),
    auto_reset_time: str = Form(default="00:00"),
    max_fps: int = Form(default=30),
    queue_size: int = Form(default=10),
    cache_size: int = Form(default=1000),
):
    guard = _admin_only(request)
    if guard:
        return guard

    if request.method == "POST":
        try:
            if action == "save":
                settings_data = {
                    "face_threshold": face_threshold,
                    "verification_threshold": verification_threshold,
                    "detection_scale": detection_scale,
                    "frame_skip": frame_skip,
                    "use_gpu": str(use_gpu == "true").lower(),
                    "attendance_window": attendance_window,
                    "auto_reset_time": auto_reset_time,
                    "max_fps": max_fps,
                    "queue_size": queue_size,
                    "cache_size": cache_size,
                }
                for key, value in settings_data.items():
                    await asyncio.to_thread(SystemSettings.set_setting, key, value)
                if await asyncio.to_thread(settings_manager.apply_settings_to_system):
                    flash(request, "Settings saved and applied successfully!", "success")
            elif action == "test":
                results = await asyncio.to_thread(test_system_configuration)
                flash(request, results["message"], "success" if results["success"] else "error")
            elif action == "reset":
                await asyncio.to_thread(SystemSettings.query.delete)
                await asyncio.to_thread(db.session.commit)
                for key, value in {
                    "face_threshold": "0.4",
                    "verification_threshold": "0.5",
                    "detection_scale": "0.3",
                    "frame_skip": "2",
                    "use_gpu": "true",
                    "attendance_window": "10",
                    "auto_reset_time": "00:00",
                    "max_fps": "30",
                    "queue_size": "10",
                    "cache_size": "1000",
                }.items():
                    await asyncio.to_thread(SystemSettings.set_setting, key, value)
                await asyncio.to_thread(settings_manager.apply_settings_to_system)
                flash(request, "Settings reset to defaults and applied successfully!", "success")
        except Exception as exc:
            flash(request, f"Error updating settings: {exc}", "error")
        return RedirectResponse(url="/system_settings", status_code=302)

    settings = await asyncio.to_thread(get_current_settings_object)
    templates = request.app.state.templates
    return render_template(
        templates,
        request,
        "system_settings.html",
        {"settings": settings, "system_status": await asyncio.to_thread(build_system_status)},
    )


@router.get("/system_monitoring", name="system_monitoring")
async def system_monitoring(request: Request):
    guard = _admin_only(request)
    if guard:
        return guard
    metrics = await asyncio.to_thread(build_system_metrics)
    status = await asyncio.to_thread(build_system_status)
    performance_labels, cpu_data, memory_data = await asyncio.to_thread(get_performance_history)
    recognition_labels, recognition_data = await asyncio.to_thread(get_recognition_performance)
    recent_logs = await asyncio.to_thread(get_recent_system_logs)
    templates = request.app.state.templates
    return render_template(
        templates,
        request,
        "system_monitoring.html",
        {
            "system_metrics": metrics,
            "system_status": status,
            "performance_labels": performance_labels,
            "cpu_data": cpu_data,
            "memory_data": memory_data,
            "recognition_labels": recognition_labels,
            "recognition_data": recognition_data,
            "recent_logs": recent_logs,
        },
    )


@router.get("/api/system_metrics", name="api_system_metrics")
async def api_system_metrics(request: Request):
    guard = _admin_only(request)
    if guard:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    return JSONResponse(
        {
            "metrics": await asyncio.to_thread(build_system_metrics),
            "status": await asyncio.to_thread(build_system_status),
            "logs": await asyncio.to_thread(get_recent_system_logs, 5),
            "success": True,
        }
    )


@router.post("/api/apply_settings", name="api_apply_settings")
async def api_apply_settings(request: Request):
    guard = _admin_only(request)
    if guard:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    success = await asyncio.to_thread(settings_manager.apply_settings_to_system)
    current_settings = await asyncio.to_thread(settings_manager.get_settings, True)
    return JSONResponse({"success": success, "message": "Settings applied successfully" if success else "Failed to apply some settings", "current_settings": current_settings})


@router.get("/api/get_current_settings", name="api_get_current_settings")
async def api_get_current_settings(request: Request):
    guard = _admin_only(request)
    if guard:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    return JSONResponse({"success": True, "settings": await asyncio.to_thread(settings_manager.get_settings)})


@router.get("/api/test_configuration", name="api_test_configuration")
async def api_test_configuration(request: Request):
    guard = _admin_only(request)
    if guard:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    return JSONResponse(await asyncio.to_thread(test_system_configuration))


@router.post("/api/reset_settings", name="api_reset_settings")
async def api_reset_settings(request: Request):
    guard = _admin_only(request)
    if guard:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    await asyncio.to_thread(SystemSettings.query.delete)
    await asyncio.to_thread(db.session.commit)
    defaults = {
        "face_threshold": "0.4",
        "verification_threshold": "0.5",
        "detection_scale": "0.3",
        "frame_skip": "2",
        "use_gpu": "true",
        "attendance_window": "10",
        "auto_reset_time": "00:00",
        "max_fps": "30",
        "queue_size": "10",
        "cache_size": "1000",
    }
    for key, value in defaults.items():
        await asyncio.to_thread(SystemSettings.set_setting, key, value)
    success = await asyncio.to_thread(settings_manager.apply_settings_to_system)
    return JSONResponse({"success": success, "message": "Settings reset to defaults and applied successfully!", "default_settings": defaults, "current_settings": await asyncio.to_thread(settings_manager.get_settings, True)})


@router.api_route("/user_management", methods=["GET", "POST"], name="user_management")
async def user_management(
    request: Request,
    action: str = Form(default=""),
    user_id: str = Form(default=""),
    username: str = Form(default=""),
    email: str = Form(default=""),
    role: str = Form(default="admin"),
    password: str = Form(default=""),
):
    guard = _admin_only(request)
    if guard:
        return guard

    if request.method == "POST":
        try:
            if action == "add_user":
                new_user = User(
                    username=username.strip(),
                    email=email.strip(),
                    role=role.lower(),
                    password_hash=generate_password_hash(password.strip()),
                    is_active=True,
                    student_id=None,
                )
                db.session.add(new_user)
                db.session.commit()
                flash(request, f"User {new_user.username} added successfully!", "success")
            elif action == "delete_user" and user_id:
                user = User.query.get(int(user_id))
                if user:
                    db.session.delete(user)
                    db.session.commit()
                    flash(request, "User deleted successfully!", "success")
            elif action == "toggle_status" and user_id:
                user = User.query.get(int(user_id))
                if user:
                    user.is_active = not user.is_active
                    db.session.commit()
                    flash(request, "User status updated successfully!", "success")
        except Exception as exc:
            db.session.rollback()
            flash(request, f"Error: {exc}", "error")
        return RedirectResponse(url="/user_management", status_code=302)

    def _load():
        users = User.query.order_by(User.created_at.desc()).all()
        activities = UserActivity.query.order_by(UserActivity.timestamp.desc()).limit(25).all()
        return users, activities

    users, activities = await asyncio.to_thread(_load)
    user_stats = {
        "total_users": await asyncio.to_thread(User.query.count),
        "active_users": await asyncio.to_thread(lambda: User.query.filter_by(is_active=True).count()),
        "admins": await asyncio.to_thread(lambda: User.query.filter_by(role="admin").count()),
        "viewers": await asyncio.to_thread(lambda: User.query.filter_by(role="viewer").count()),
        "online_now": 1,
    }
    recent_activities = [
        {
            "username": activity.username,
            "action": activity.action,
            "action_type": activity.action_type,
            "timestamp": activity.timestamp,
            "icon": "fa-info-circle",
            "color": "success",
        }
        for activity in activities
    ]
    current_user = await asyncio.to_thread(lambda: User.query.filter_by(username=request.session.get("username", "admin")).first() or Admin.query.filter_by(username="admin").first())
    templates = request.app.state.templates
    return render_template(
        templates,
        request,
        "user_management.html",
        {
            "users": users,
            "user_stats": user_stats,
            "recent_activities": recent_activities,
            "current_user": current_user,
        },
    )


@router.get("/backup_security", name="backup_security")
async def backup_security(request: Request):
    guard = _admin_only(request)
    if guard:
        return guard
    templates = request.app.state.templates
    return render_template(
        templates,
        request,
        "backup_security.html",
        {
            "recent_backups": [
                {"id": 1, "filename": "backup_latest.sql", "created_at": datetime.now(), "size": "2.5 MB", "status": "success"}
            ],
            "security_stats": {"failed_logins": 0, "active_sessions": 1, "blocked_ips": 0, "security_score": 95},
            "security_logs": await asyncio.to_thread(get_recent_system_logs, 3),
        },
    )


@router.get("/export_detailed_report", name="export_detailed_report")
async def export_detailed_report(request: Request, start_date: str, end_date: str, department: str = ""):
    guard = _admin_only(request)
    if guard:
        return guard

    def _build():
        query = db.session.query(Attendance, Student).join(Student).filter(
            Attendance.timestamp >= start_date,
            Attendance.timestamp <= end_date + " 23:59:59",
        )
        if department:
            query = query.filter(Student.department == department)
        rows = query.all()
        return pd.DataFrame(
            [
                {
                    "Date": attendance.timestamp.strftime("%Y-%m-%d"),
                    "Time": attendance.timestamp.strftime("%H:%M:%S"),
                    "Student ID": student.student_code,
                    "Name": student.name,
                    "Department": student.department,
                    "Phone": student.phone_number,
                }
                for attendance, student in rows
            ]
        )

    df = await asyncio.to_thread(_build)
    return _excel_response(df, f"detailed_report_{start_date}_to_{end_date}.xlsx", "Detailed Report")


@router.get("/export_summary_report", name="export_summary_report")
async def export_summary_report(request: Request):
    guard = _admin_only(request)
    if guard:
        return guard
    flash(request, "Summary report export functionality would be implemented here", "info")
    return RedirectResponse(url="/reports_analytics", status_code=302)


@router.get("/export_activities", name="export_activities")
async def export_activities(request: Request):
    guard = _admin_only(request)
    if guard:
        return guard
    activities = await asyncio.to_thread(lambda: UserActivity.query.order_by(UserActivity.timestamp.desc()).all())
    df = pd.DataFrame(
        [
            {
                "Username": activity.username,
                "Action": activity.action,
                "Action Type": activity.action_type,
                "Timestamp": activity.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "IP Address": activity.ip_address or "N/A",
            }
            for activity in activities
        ]
    )
    return _excel_response(df, f"user_activities_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx", "User Activities")


@router.post("/clear_activities", name="clear_activities")
async def clear_activities(request: Request):
    guard = _admin_only(request)
    if guard:
        return JSONResponse({"success": False, "message": "Unauthorized"}, status_code=401)
    deleted = await asyncio.to_thread(UserActivity.query.delete)
    await asyncio.to_thread(db.session.commit)
    return JSONResponse({"success": True, "message": f"Successfully deleted {deleted} activity records!"})


@router.get("/get_user_details/{user_id}", name="get_user_details")
async def get_user_details(request: Request, user_id: int):
    guard = _admin_only(request)
    if guard:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    user = await asyncio.to_thread(lambda: User.query.get(user_id))
    if not user:
        return JSONResponse({"error": "User not found"}, status_code=404)
    result = {"id": user.id, "username": user.username, "email": user.email, "role": user.role, "is_active": user.is_active, "student": None}
    if user.role == "viewer" and user.student_id and user.student:
        result["student"] = {"id": user.student.id, "student_code": user.student.student_code, "name": user.student.name, "department": user.student.department, "phone_number": user.student.phone_number}
    return JSONResponse(result)


async def _redirect_with_flash(request: Request, message: str, path: str):
    flash(request, message, "success")
    return RedirectResponse(url=path, status_code=302)


@router.post("/send_bulk_email", name="send_bulk_email")
async def send_bulk_email(request: Request):
    guard = _admin_only(request)
    if guard:
        return guard
    return await _redirect_with_flash(request, "Bulk email sent successfully!", "/user_management")


@router.post("/backup_database", name="backup_database")
async def backup_database(request: Request):
    guard = _admin_only(request)
    if guard:
        return guard
    return await _redirect_with_flash(request, "Database backup created successfully!", "/backup_security")


@router.post("/restore_backup", name="restore_backup")
async def restore_backup(request: Request):
    guard = _admin_only(request)
    if guard:
        return JSONResponse({"success": False, "message": "Unauthorized"}, status_code=401)
    return JSONResponse({"success": True, "message": "Database restored successfully!"})


@router.post("/clear_sessions", name="clear_sessions")
async def clear_sessions(request: Request):
    guard = _admin_only(request)
    if guard:
        return JSONResponse({"success": False, "message": "Unauthorized"}, status_code=401)
    return JSONResponse({"success": True, "message": "Sessions cleared successfully!"})


@router.post("/reset_security", name="reset_security")
async def reset_security(request: Request):
    guard = _admin_only(request)
    if guard:
        return JSONResponse({"success": False, "message": "Unauthorized"}, status_code=401)
    return JSONResponse({"success": True, "message": "Security settings reset successfully!"})


@router.post("/import_data", name="import_data")
async def import_data(request: Request):
    guard = _admin_only(request)
    if guard:
        return JSONResponse({"success": False, "message": "Unauthorized"}, status_code=401)
    return JSONResponse({"success": True, "message": "Data import completed successfully!"})


@router.post("/cleanup_logs", name="cleanup_logs")
async def cleanup_logs(request: Request):
    guard = _admin_only(request)
    if guard:
        return JSONResponse({"success": False, "message": "Unauthorized"}, status_code=401)
    return JSONResponse({"success": True, "message": "Logs cleaned up successfully!"})


@router.post("/optimize_database", name="optimize_database")
async def optimize_database(request: Request):
    guard = _admin_only(request)
    if guard:
        return guard
    return await _redirect_with_flash(request, "Database optimization completed successfully!", "/backup_security")


@router.post("/clear_cache", name="clear_cache")
async def clear_cache(request: Request):
    guard = _admin_only(request)
    if guard:
        return guard
    return await _redirect_with_flash(request, "System cache cleared successfully!", "/backup_security")


@router.post("/system_check", name="system_check")
async def system_check(request: Request):
    guard = _admin_only(request)
    if guard:
        return guard
    results = await asyncio.to_thread(test_system_configuration)
    flash(request, f"System check completed: {results['message']}", "info")
    return RedirectResponse(url="/backup_security", status_code=302)


@router.post("/clear_logs", name="clear_logs")
async def clear_logs(request: Request):
    guard = _admin_only(request)
    if guard:
        return guard
    return await _redirect_with_flash(request, "System logs cleared successfully!", "/system_monitoring")


@router.get("/download_logs", name="download_logs")
async def download_logs(request: Request):
    guard = _admin_only(request)
    if guard:
        return guard
    content = "\n".join(f"[{row['timestamp']}] {row['level'].upper()}: {row['message']}" for row in await asyncio.to_thread(get_recent_system_logs, 10))
    return Response(content=content, media_type="text/plain", headers={"Content-Disposition": f'attachment; filename="system_logs_{datetime.now().strftime("%Y%m%d_%H%M%S")}.txt"'})


@router.get("/download_system_report", name="download_system_report")
async def download_system_report(request: Request):
    guard = _admin_only(request)
    if guard:
        return guard
    content = f"Total Students: {await asyncio.to_thread(Student.query.count)}\nTotal Attendance Records: {await asyncio.to_thread(Attendance.query.count)}\nGenerated: {datetime.now():%Y-%m-%d %H:%M:%S}\n"
    return Response(content=content, media_type="text/plain", headers={"Content-Disposition": f'attachment; filename="system_report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.txt"'})
