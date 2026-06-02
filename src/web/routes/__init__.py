from .admin import router as admin_router
from .attendance import router as attendance_router
from .auth import router as auth_router
from .lock import router as lock_router
from .public import router as public_router
from .students import router as students_router
from .system import router as system_router
from .viewer import router as viewer_router

__all__ = [
    "admin_router",
    "attendance_router",
    "auth_router",
    "lock_router",
    "public_router",
    "students_router",
    "system_router",
    "viewer_router",
]
