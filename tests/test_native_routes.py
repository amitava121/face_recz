import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.mark.asyncio
async def test_native_fastapi_root_redirects_to_locked_by_default():
    from src.web.app import create_app

    app = create_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/locked"


@pytest.mark.asyncio
async def test_native_fastapi_locked_page_renders_html():
    from src.web.app import create_app

    app = create_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/locked")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "System Locked" in response.text


@pytest.mark.asyncio
async def test_native_fastapi_attendance_redirects_to_locked_when_system_is_locked():
    from src.web.app import create_app

    app = create_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/attendance", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/locked"


@pytest.mark.asyncio
async def test_native_fastapi_lock_endpoint_redirects_to_locked():
    from src.web.app import create_app

    app = create_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post("/lock", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/locked"
