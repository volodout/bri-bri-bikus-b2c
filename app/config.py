from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql://neomarket:neomarket@localhost:5432/neomarket",
    )
    jwt_algorithm: str = os.getenv("JWT_ALGORITHM", "HS256")
    jwt_secret: str = os.getenv("JWT_SECRET", "dev-jwt-secret-for-tests-32-bytes")
    jwt_public_key: str = os.getenv("JWT_PUBLIC_KEY", "")


settings = Settings()
