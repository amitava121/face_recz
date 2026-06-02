from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from src.models.db import Student, create_tables_if_missing, db
from src.services.webrtc_server import configure_runtime_hooks

from .config import Settings
from .realtime import app as realtime_app
from .routes import (
    admin_router,
    attendance_router,
    auth_router,
    lock_router,
    public_router,
    students_router,
    system_router,
    viewer_router,
)
from .services.attendance import mark_attendance_for_student, register_face_embedding
from .templates import build_templates


def create_app() -> FastAPI:
    settings = Settings.from_env()

    app = FastAPI(
        title="Face Recognition Attendance System",
        version="2.0.0",
    )
    db.init_app()
    create_tables_if_missing()
    app.state.settings = settings
    app.state.system_locked = True
    app.state.attendance_running = False
    app.state.registration_complete = {}
    app.state.active_liveness_challenges = {}
    app.state.templates = build_templates()
    app.add_middleware(SessionMiddleware, secret_key=settings.secret_key)

    def _mark_attendance(student_id: int, confidence: float = 0.0, device_id: str | None = None, **metadata):
        payload, _ = mark_attendance_for_student(student_id, confidence, device_id, **metadata)
        return payload

    def _register_face(student_id: int, embedding: list[float]):
        payload, _ = register_face_embedding(student_id, embedding, app.state.registration_complete)
        return payload

    configure_runtime_hooks(
        attendance_callback=_mark_attendance,
        register_face_callback=_register_face,
        get_students_callback=lambda: [
            {
                "id": student.id,
                "name": student.name,
                "student_code": student.student_code,
                "face_embedding": student.face_embedding_array,
                "embedding_model_version": student.embedding_model_version,
            }
            for student in Student.query.filter(Student.face_embedding_array.isnot(None)).all()
        ],
    )

    static_dir = Path(__file__).resolve().parents[1] / "app" / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(public_router)
    app.include_router(auth_router)
    app.include_router(lock_router)
    app.include_router(students_router)
    app.include_router(attendance_router)
    app.include_router(admin_router)
    app.include_router(viewer_router)
    app.include_router(system_router)
    app.mount("/socket.io", realtime_app)

    @app.get("/health")
    async def healthcheck() -> dict[str, str]:
        return {
            "status": "ok",
            "app": "fastapi",
            "mode": "native-async",
        }

    return app
