from __future__ import annotations

import time
from uuid import uuid4

import httpx
import jwt
import pytest

from app.main import create_app
from app.products import (
    Category,
    InMemoryProductRepository,
    Product,
    ProductImage,
    ProductStatus,
)
from app.moderation import RecordingModerationGateway
from app.skus import InMemorySkuRepository, Sku

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
def sku_repository() -> InMemorySkuRepository:
    return InMemorySkuRepository()


@pytest.fixture
def moderation_gateway() -> RecordingModerationGateway:
    return RecordingModerationGateway()


@pytest.fixture
def client(
    product_repository: InMemoryProductRepository,
    sku_repository: InMemorySkuRepository,
    moderation_gateway: RecordingModerationGateway,
):
    app = create_app(
        product_repository=product_repository,
        sku_repository=sku_repository,
        moderation_gateway=moderation_gateway,
    )
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://b2b.test")


async def seed_product(
    repository: InMemoryProductRepository,
    *,
    status: ProductStatus = ProductStatus.CREATED,
    seller_id: str = SELLER_ID,
    product_id: str | None = None,
) -> Product:
    product = Product(
        id=product_id or str(uuid4()),
        seller_id=seller_id,
        category=Category(id=CATEGORY_ID, name="iOS"),
        title="iPhone 15 Pro Max",
        slug="iphone-15-pro-max",
        description="Flagship Apple smartphone with A17 Pro chip",
        status=status,
        deleted=False,
        images=(ProductImage(id=str(uuid4()), url="/s3/iphone15-front.jpg", ordering=0),),
        characteristics=(),
        skus=(),
    )
    return await repository.create_product(product)


def valid_sku_payload(**overrides):
    payload = {
        "product_id": str(uuid4()),
        "name": "256GB Black",
        "price": 12999000,
        "cost_price": 9500000,
        "discount": 0,
        "image": "/s3/iphone15-black-256.jpg",
        "characteristics": [
            {"name": "Цвет", "value": "Чёрный"},
            {"name": "Объём памяти", "value": "256 ГБ"},
        ],
    }
    payload.update(overrides)
    return payload


async def seed_sku(
    sku_repository: InMemorySkuRepository,
    *,
    product_id: str,
    sku_id: str | None = None,
    reserved_quantity: int = 0,
    active_quantity: int = 0,
) -> Sku:
    sku = Sku(
        id=sku_id or str(uuid4()),
        product_id=product_id,
        name="256GB Black",
        price=12999000,
        cost_price=9500000,
        discount=0,
        image="/s3/iphone15-black-256.jpg",
        characteristics=(),
        active_quantity=active_quantity,
        reserved_quantity=reserved_quantity,
    )
    return await sku_repository.create_sku(sku)


def valid_product_update_payload(**overrides):
    payload = {
        "title": "iPhone 15 Pro Max (обновлено)",
        "description": "Обновленное описание флагмана Apple",
        "category_id": CATEGORY_ID,
        "images": [{"url": "/s3/iphone15-front-v2.jpg", "ordering": 0}],
        "characteristics": [{"name": "Бренд", "value": "Apple"}],
    }
    payload.update(overrides)
    return payload


def valid_sku_update_payload(**overrides):
    payload = {
        "name": "256GB Black Titanium",
        "price": 13499000,
        "cost_price": 9800000,
        "discount": 500000,
        "image": "/s3/iphone15-black-titanium.jpg",
        "characteristics": [
            {"name": "Цвет", "value": "Чёрный титан"},
            {"name": "Объём памяти", "value": "256 ГБ"},
        ],
    }
    payload.update(overrides)
    return payload
