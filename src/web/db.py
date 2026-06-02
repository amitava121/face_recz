from __future__ import annotations

import os
from dataclasses import dataclass


def _to_asyncpg_url(database_url: str) -> str:
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+asyncpg://", 1)
    return database_url


@dataclass(slots=True)
class DatabaseSettings:
    async_database_url: str

    @classmethod
    def from_env(cls) -> "DatabaseSettings":
        database_url = os.getenv("DATABASE_URL")
        if database_url:
            return cls(async_database_url=_to_asyncpg_url(database_url))

        user = os.getenv("DB_USER", "postgres")
        password = os.getenv("DB_PASS", "")
        host = os.getenv("DB_HOST", "localhost")
        port = os.getenv("DB_PORT", "5432")
        name = os.getenv("DB_NAME", "attendance_db")
        return cls(
            async_database_url=(
                f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{name}"
            )
        )
