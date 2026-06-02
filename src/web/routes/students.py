from __future__ import annotations

import asyncio

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from werkzeug.security import generate_password_hash

from src.models.db import Student, User, db
from src.web.session import flash
from src.web.templates import render_template


router = APIRouter()


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
        request.session["registering_student_id"] = created_student_id
        request.app.state.registration_complete.pop(created_student_id, None)
        return RedirectResponse(url="/capture_face", status_code=302)
    except Exception as exc:
        db.session.rollback()
        flash(request, f"Registration error: {exc}", "error")
        return RedirectResponse(url="/register", status_code=302)


@router.get("/capture_face", name="capture_face")
async def capture_face(request: Request):
    student_id = request.session.get("registering_student_id")
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
