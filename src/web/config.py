from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(slots=True)
class Settings:
    secret_key: str
    host: str
    port: int

    @classmethod
    def from_env(cls) -> "Settings":
        secret_key = (
            os.getenv("APP_SECRET_KEY")
            or os.getenv("FLASK_SECRET_KEY")
            or os.getenv("SECRET_KEY")
            or "default_secret_key"
        )
        host = os.getenv("APP_HOST", "0.0.0.0")
        port = int(os.getenv("APP_PORT", "5001"))
        return cls(secret_key=secret_key, host=host, port=port)
