import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def test_async_database_url_builds_from_legacy_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("DB_USER", "postgres")
    monkeypatch.setenv("DB_PASS", "secret")
    monkeypatch.setenv("DB_HOST", "db.internal")
    monkeypatch.setenv("DB_PORT", "5433")
    monkeypatch.setenv("DB_NAME", "attendance_db")

    from src.web.db import DatabaseSettings

    settings = DatabaseSettings.from_env()

    assert settings.async_database_url == (
        "postgresql+asyncpg://postgres:secret@db.internal:5433/attendance_db"
    )


def test_async_database_url_upgrades_sync_postgres_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/appdb")

    from src.web.db import DatabaseSettings

    settings = DatabaseSettings.from_env()

    assert settings.async_database_url == (
        "postgresql+asyncpg://user:pass@localhost:5432/appdb"
    )


def test_async_database_url_enforces_ssl_for_supabase(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://postgres:secret@db.exampleproject.supabase.co:5432/postgres",
    )

    from src.web.db import DatabaseSettings

    settings = DatabaseSettings.from_env()

    assert settings.async_database_url == (
        "postgresql+asyncpg://postgres:secret@db.exampleproject.supabase.co:5432/postgres?sslmode=require"
    )


def test_startup_schema_migrations_default_off_for_supabase(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://postgres:secret@db.exampleproject.supabase.co:5432/postgres?sslmode=require",
    )
    monkeypatch.delenv("ENABLE_STARTUP_SCHEMA_MIGRATIONS", raising=False)

    from src.models.db import _startup_schema_migrations_enabled

    assert _startup_schema_migrations_enabled() is False


def test_startup_schema_migrations_can_be_forced_on(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://postgres:secret@db.exampleproject.supabase.co:5432/postgres?sslmode=require",
    )
    monkeypatch.setenv("ENABLE_STARTUP_SCHEMA_MIGRATIONS", "true")

    from src.models.db import _startup_schema_migrations_enabled

    assert _startup_schema_migrations_enabled() is True
