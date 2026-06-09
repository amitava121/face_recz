from __future__ import annotations

import asyncio

from fastapi import APIRouter, Form, Query, Request
from loguru import logger
from fastapi.responses import JSONResponse, RedirectResponse
from werkzeug.security import generate_password_hash

from src.models.db import Student, User, db
from src.web.session import flash
from src.web.templates import render_template


router = APIRouter()


def _admin_only(request: Request):
    if not request.session.get("admin_logged_in"):
        if request.session.get("viewer_logged_in"):
            return RedirectResponse(url="/viewer_dashboard", status_code=302)
        return RedirectResponse(url="/login", status_code=302)
    return None


def _register_student(student_id: str, name: str, department: str, phone_number: str, password: str) -> int:
    student = Student(
        student_code=student_id.strip(),
        name=name.strip(),
        department=department.strip(),
        phone_number=phone_number,
    )
    db.session.add(student)
    db.session.flush()

    viewer_username = student_id.strip().lower()
    viewer_email = f"{viewer_username}@student.edu"
    viewer = User(
        username=viewer_username,
        password_hash=generate_password_hash(password),
        email=viewer_email,
        role="viewer",
        student_id=student.id,
        is_active=True,
    )
    db.session.add(viewer)
    db.session.commit()
    return student.id


@router.get("/register", name="register")
async def register_page(request: Request, success: str | None = Query(default=None)):
    if request.session.get("viewer_logged_in"):
        return RedirectResponse(url="/viewer_dashboard", status_code=302)
    if success == "true":
        flash(request, "Student registered successfully!", "success")
    templates = request.app.state.templates
    return render_template(templates, request, "register.html", {})


@router.post("/register", name="register_post")
async def register_submit(
    request: Request,
    student_id: str = Form(...),
    name: str = Form(...),
    department: str = Form(...),
    phone_number: str = Form(default=""),
    password: str = Form(default=""),
    confirm_password: str = Form(default=""),
):
    if request.session.get("viewer_logged_in"):
        return RedirectResponse(url="/viewer_dashboard", status_code=302)
    phone_number = phone_number.strip()
    password = password.strip()
    confirm_password = confirm_password.strip()

    if len(phone_number) != 10 or not phone_number.isdigit():
        flash(request, "Phone number must be exactly 10 digits.", "error")
        return RedirectResponse(url="/register", status_code=302)

    if not password or len(password) < 6:
        flash(request, "Password must be at least 6 characters long.", "error")
        return RedirectResponse(url="/register", status_code=302)

    if password != confirm_password:
        flash(request, "Passwords do not match.", "error")
        return RedirectResponse(url="/register", status_code=302)

    existing_student = await asyncio.to_thread(lambda: Student.query.filter_by(student_code=student_id).first())
    if existing_student:
        flash(request, f"Student ID {student_id} is already registered.", "error")
        return RedirectResponse(url="/register", status_code=302)

    existing_user = await asyncio.to_thread(lambda: User.query.filter_by(username=student_id.strip().lower()).first())
    if existing_user:
        flash(request, f"Username {student_id} is already taken. Please contact administrator.", "error")
        return RedirectResponse(url="/register", status_code=302)

    try:
        created_student_id = await asyncio.to_thread(
            _register_student, student_id, name, department, phone_number, password
        )
        logger.info(f"Registration created student_id={created_student_id}, session before set: {dict(request.session)}")
        request.session["registering_student_id"] = created_student_id
        logger.info(f"Session after set: {dict(request.session)}")
        request.app.state.registration_complete.pop(created_student_id, None)
        response = RedirectResponse(url="/capture_face", status_code=302)
        logger.info(f"Redirecting to /capture_face with session: {dict(request.session)}")
        return response
    except Exception as exc:
        db.session.rollback()
        flash(request, f"Registration error: {exc}", "error")
        return RedirectResponse(url="/register", status_code=302)


@router.get("/capture_face", name="capture_face")
async def capture_face(request: Request):
    logger.info(f"capture_face called, session contents: {dict(request.session)}")
    if request.session.get("viewer_logged_in"):
        return RedirectResponse(url="/viewer_dashboard", status_code=302)
    student_id = request.session.get("registering_student_id")
    logger.info(f"capture_face student_id from session: {student_id}")
    if not student_id:
        flash(request, "Please register student details first.", "error")
        return RedirectResponse(url="/register", status_code=302)

    request.app.state.registration_complete.pop(student_id, None)
    templates = request.app.state.templates
    return render_template(templates, request, "webrtc_capture.html", {"student_id": student_id})


@router.get("/students", name="list_students")
async def list_students(
    request: Request,
    search: str = Query(default=""),
    page: int = Query(default=1),
):
    guard = _admin_only(request)
    if guard:
        return guard
    search = search.strip()
    per_page = 50

    def _load_students():
        if search:
            query = Student.query.filter(
                (Student.student_code.ilike(f"%{search}%")) | (Student.name.ilike(f"%{search}%"))
            ).order_by(Student.student_code.asc())
        else:
            query = Student.query.order_by(Student.student_code.asc())
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        return pagination

    pagination = await asyncio.to_thread(_load_students)
    templates = request.app.state.templates
    return render_template(
        templates,
        request,
        "student_list.html",
        {
            "students": pagination.items,
            "pagination": pagination,
            "search": search,
        },
    )


@router.get("/api/students", name="api_students")
async def api_students():
    def _load_students():
        students = Student.query.filter(Student.face_embedding_array.isnot(None)).all()
        return [
            {
                "id": student.id,
                "name": student.name,
                "student_code": student.student_code,
                "face_embedding": student.face_embedding_array,
            }
            for student in students
            if student.face_embedding_array
        ]

    return JSONResponse(await asyncio.to_thread(_load_students))


# ── Edit Student ─────────────────────────────────────────────────────────────

@router.post("/edit_student/{student_id}", name="edit_student")
async def edit_student(
    request: Request,
    student_id: int,
    student_code: str = Form(...),
    name: str = Form(...),
    department: str = Form(...),
    phone_number: str = Form(default=""),
):
    guard = _admin_only(request)
    if guard:
        return guard
    def _do_edit():
        student = Student.query.get(student_id)
        if not student:
            return False, "Student not found."

        # Check uniqueness of student_code if it changed
        if student.student_code != student_code.strip():
            existing = Student.query.filter_by(student_code=student_code.strip()).first()
            if existing and existing.id != student_id:
                return False, f"Student code {student_code} already exists."

        student.student_code = student_code.strip()
        student.name = name.strip()
        student.department = department.strip()
        student.phone_number = phone_number.strip()
        db.session.commit()
        return True, "Student updated successfully!"

    ok, message = await asyncio.to_thread(_do_edit)
    flash(request, message, "success" if ok else "error")
    return RedirectResponse(url="/students", status_code=302)


# ── Delete helpers ───────────────────────────────────────────────────────────

def _delete_student_details_only(student_ids: list[int]) -> tuple[int, list[str]]:
    """Delete student records, face images, and user accounts.

    Attendance records are KEPT — only the FK reference (student_id) is set to
    NULL.  The denormalized columns (student_code, student_name,
    student_department) already stored on each attendance row preserve history.
    """
    from src.models.db import Attendance, FaceImage, User

    deleted_names: list[str] = []
    for sid in student_ids:
        student = Student.query.get(sid)
        if not student:
            continue
        deleted_names.append(student.name)

        # Detach attendance records (preserve history)
        Attendance.query.filter_by(student_id=sid).update({"student_id": None})

        # Remove face images (binary blobs, no need to keep)
        FaceImage.query.filter_by(student_id=sid).delete()

        # Remove linked viewer user account
        User.query.filter_by(student_id=sid).delete()

        db.session.delete(student)

    db.session.commit()
    return len(deleted_names), deleted_names


def _delete_students_and_attendance(student_ids: list[int]) -> tuple[int, list[str]]:
    """Delete student records, face images, user accounts, AND attendance records."""
    from src.models.db import Attendance, FaceImage, User

    deleted_names: list[str] = []
    for sid in student_ids:
        student = Student.query.get(sid)
        if not student:
            continue
        deleted_names.append(student.name)

        # Remove attendance records
        Attendance.query.filter_by(student_id=sid).delete()

        # Remove face images
        FaceImage.query.filter_by(student_id=sid).delete()

        # Remove linked viewer user account
        User.query.filter_by(student_id=sid).delete()

        db.session.delete(student)

    db.session.commit()
    return len(deleted_names), deleted_names


# ── Delete selected students (details only — keep attendance) ────────────────

@router.post("/delete_selected_student_details", name="delete_selected_student_details")
async def delete_selected_student_details(request: Request):
    guard = _admin_only(request)
    if guard:
        return guard
    form = await request.form()
    student_ids = [int(sid) for sid in form.getlist("student_ids")]

    if not student_ids:
        flash(request, "No students selected for deletion.", "error")
        return RedirectResponse(url="/students", status_code=302)

    try:
        count, names = await asyncio.to_thread(_delete_student_details_only, student_ids)
        flash(request, f"Deleted {count} student(s): {', '.join(names)}. Attendance history preserved.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(request, f"Delete failed: {exc}", "error")

    return RedirectResponse(url="/students", status_code=302)


# ── Delete selected students AND their attendance ────────────────────────────

@router.post("/delete_selected_students_and_attendance", name="delete_selected_students_and_attendance")
async def delete_selected_students_and_attendance(request: Request):
    guard = _admin_only(request)
    if guard:
        return guard
    form = await request.form()
    student_ids = [int(sid) for sid in form.getlist("student_ids")]

    if not student_ids:
        flash(request, "No students selected for deletion.", "error")
        return RedirectResponse(url="/students", status_code=302)

    try:
        count, names = await asyncio.to_thread(_delete_students_and_attendance, student_ids)
        flash(request, f"Deleted {count} student(s) and their attendance records: {', '.join(names)}.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(request, f"Delete failed: {exc}", "error")

    return RedirectResponse(url="/students", status_code=302)


# ── Export students to Excel ─────────────────────────────────────────────────

@router.get("/export_students", name="export_students")
async def export_students(request: Request):
    guard = _admin_only(request)
    if guard:
        return guard
    import pandas as pd
    from io import BytesIO
    from fastapi.responses import StreamingResponse

    def _build_excel():
        students = Student.query.order_by(Student.student_code.asc()).all()
        data = [
            {
                "Roll Code": s.student_code,
                "Name": s.name,
                "Department": s.department,
                "Phone": s.phone_number or "",
                "Face Registered": "Yes" if s.face_embedding_array else "No",
            }
            for s in students
        ]
        df = pd.DataFrame(data)
        output = BytesIO()
        with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
            df.to_excel(writer, index=False, sheet_name="Students")
        output.seek(0)
        return output

    excel_buffer = await asyncio.to_thread(_build_excel)
    return StreamingResponse(
        excel_buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="students_export.xlsx"'},
    )
