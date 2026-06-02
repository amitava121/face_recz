from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.templating import Jinja2Templates
from jinja2 import pass_context
from starlette.requests import Request
from starlette.responses import Response

from .session import consume_flashes


TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "app" / "templates"
ANONYMOUS_USER = SimpleNamespace(is_authenticated=False, is_anonymous=True, role=None)


def _session_user(request: Request):
    if request.session.get("user_id"):
        return SimpleNamespace(
            is_authenticated=True,
            is_anonymous=False,
            role=request.session.get("role"),
            username=request.session.get("username"),
            id=request.session.get("user_id"),
        )
    return ANONYMOUS_USER


def build_templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

    @pass_context
    def template_url_for(context, endpoint: str, **params: str) -> str:
        request: Request = context["request"]
        if endpoint == "static" and "filename" in params and "path" not in params:
            params["path"] = params.pop("filename")
        return str(request.url_for(endpoint, **params))

    @pass_context
    def flashed_messages(context, with_categories: bool = False):
        request: Request = context["request"]
        return consume_flashes(request, with_categories=with_categories)

    templates.env.globals["url_for"] = template_url_for
    templates.env.globals["get_flashed_messages"] = flashed_messages
    return templates


def render_template(
    templates: Jinja2Templates,
    request: Request,
    template_name: str,
    context: dict[str, object] | None = None,
    status_code: int = 200,
) -> Response:
    payload = dict(context or {})
    if not hasattr(request, "args"):
        setattr(request, "args", request.query_params)
    payload.setdefault("session", request.session)
    payload.setdefault("current_user", _session_user(request))
    return templates.TemplateResponse(request, template_name, payload, status_code=status_code)
