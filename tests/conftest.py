from __future__ import annotations

import time
from uuid import uuid4

import httpx
import jwt
import pytest

from app.main import create_app
from app.products import Category, InMemoryProductRepository

SELLER_ID = "123e4567-e89b-12d3-a456-426614174000"
OTHER_SELLER_ID = "223e4567-e89b-12d3-a456-426614174000"
CATEGORY_ID = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
UNKNOWN_CATEGORY_ID = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"


def auth_headers(seller_id: str = SELLER_ID) -> dict[str, str]:
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": seller_id,
            "role": "seller",
            "iat": now,
            "exp": now + 3600,
            "jti": str(uuid4()),
        },
        "dev-jwt-secret-for-tests-32-bytes",
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def product_repository() -> InMemoryProductRepository:
    return InMemoryProductRepository(
        categories=[Category(id=CATEGORY_ID, name="iOS")]
    )


@pytest.fixture
def client(product_repository: InMemoryProductRepository):
    app = create_app(product_repository=product_repository)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://b2b.test")
