import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.mark.asyncio
async def test_native_register_page_renders():
    from src.web.app import create_app

    app = create_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/register")

    assert response.status_code == 200
    assert "Register" in response.text or "Student" in response.text


@pytest.mark.asyncio
async def test_native_students_page_renders():
    from src.web.app import create_app

    app = create_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/students", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/login"


@pytest.mark.asyncio
async def test_native_capture_face_requires_registration_session():
    from src.web.app import create_app

    app = create_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/capture_face", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/register"
