import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def disable_startup_db_bootstrap(monkeypatch):
    import src.web.app as web_app

    monkeypatch.setattr(web_app, "create_tables_if_missing", lambda: None)


@pytest.mark.asyncio
async def test_create_app_exposes_native_async_health_endpoint():
    from src.web.app import create_app

    app = create_app()

    assert isinstance(app, FastAPI)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "app": "fastapi",
        "mode": "native-async",
    }


def test_settings_reads_legacy_secret_key_env(monkeypatch):
    monkeypatch.setenv("FLASK_SECRET_KEY", "legacy-secret")
    monkeypatch.delenv("APP_SECRET_KEY", raising=False)

    from src.web.config import Settings

    settings = Settings.from_env()

    assert settings.secret_key == "legacy-secret"


def test_main_supports_fastapi_mode(monkeypatch):
    import main

    called = {}

    def fake_start_fastapi_app(port):
        called["port"] = port
        return True

    monkeypatch.setattr(main, "start_fastapi_app", fake_start_fastapi_app)
    monkeypatch.setattr(sys, "argv", ["main.py", "--mode", "fastapi", "--port", "9001"])

    assert main.main() is True
    assert called == {"port": 9001}
