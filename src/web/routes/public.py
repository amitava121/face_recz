from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from src.web.templates import render_template


router = APIRouter()


@router.get("/", name="index")
async def index(request: Request):
    if request.app.state.system_locked:
        return RedirectResponse(url="/locked", status_code=302)

    templates = request.app.state.templates
    return render_template(
        templates,
        request,
        "all_templates.html",
        {
            "show_attendance": False,
            "show_face_capture": False,
        },
    )
