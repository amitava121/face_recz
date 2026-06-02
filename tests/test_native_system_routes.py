import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.mark.asyncio
async def test_native_get_sleep_timer_returns_integer_seconds():
    from src.web.app import create_app

    app = create_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/get_sleep_timer")

    assert response.status_code == 200
    payload = response.json()
    assert "sleep_timer_seconds" in payload
    assert isinstance(payload["sleep_timer_seconds"], int)


@pytest.mark.asyncio
async def test_native_sleep_page_renders_html():
    from src.web.app import create_app

    app = create_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/sleep")

    assert response.status_code == 200
    assert "Sleep Mode" in response.text
