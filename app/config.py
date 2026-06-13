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
    moderation_url: str = os.getenv("MODERATION_URL", "http://moderation:8000")
    b2b_to_mod_key: str = os.getenv("B2B_TO_MOD_KEY", "dev-b2b-to-mod-key")
    mod_to_b2b_key: str = os.getenv("MOD_TO_B2B_KEY", "dev-mod-to-b2b-key")


settings = Settings()
