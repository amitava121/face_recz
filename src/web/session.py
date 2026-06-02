from __future__ import annotations

from typing import Any

from starlette.requests import Request


FLASH_SESSION_KEY = "_flashes"


def flash(request: Request, message: str, category: str = "message") -> None:
    flashes = list(request.session.get(FLASH_SESSION_KEY, []))
    flashes.append((category, message))
    request.session[FLASH_SESSION_KEY] = flashes


def consume_flashes(request: Request, with_categories: bool = False) -> list[Any]:
    flashes = list(request.session.pop(FLASH_SESSION_KEY, []))
    if with_categories:
        return flashes
    return [message for _, message in flashes]
