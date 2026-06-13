from __future__ import annotations

from uuid import uuid4

from app.products import BlockingReason, FieldReport, ProductStatus
from tests.conftest import (
    OTHER_SELLER_ID,
    SELLER_ID,
    auth_headers,
    seed_product,
    seed_sku,
    service_headers,
)


# --- happy path -----------------------------------------------------------


async def test_get_moderated_product_returns_full_payload(
    client, product_repository, sku_repository
):
    product = await seed_product(product_repository, status=ProductStatus.MODERATED)
    await seed_sku(
        sku_repository, product_id=product.id, active_quantity=10, reserved_quantity=2
    )

    async with client as ac:
        response = await ac.get(f"/api/v1/products/{product.id}", headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == product.id
    assert body["status"] == "MODERATED"
    assert body["blocked"] is False
    assert body["blocking_reason"] is None
    assert body["field_reports"] == []
    assert body["category"] == {"id": product.category.id, "name": "iOS"}
    assert len(body["skus"]) == 1
    sku = body["skus"][0]
    assert sku["cost_price"] == 9500000  # seller-mode field
    assert sku["reserved_quantity"] == 2  # seller-mode field
    assert sku["active_quantity"] == 10


async def test_get_blocked_product_returns_blocking_reason_and_field_reports(
    client, product_repository, sku_repository
):
    reason = BlockingReason(
        id=str(uuid4()),
        title="Описание не соответствует товару",
        comment="Несоответствие описания и фотографий",
    )
    sku_id = str(uuid4())
    reports = (
        FieldReport(field_name="description", sku_id=None, comment="Материал не совпадает"),
        FieldReport(field_name="sku_image", sku_id=sku_id, comment="Фото SKU не то"),
    )
    product = await seed_product(
        product_repository,
        status=ProductStatus.BLOCKED,
        blocking_reason=reason,
        field_reports=reports,
    )
    await seed_sku(sku_repository, product_id=product.id, sku_id=sku_id)

    async with client as ac:
        response = await ac.get(f"/api/v1/products/{product.id}", headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "BLOCKED"
    assert body["blocked"] is True
    assert body["blocking_reason"]["id"] == reason.id
    assert body["blocking_reason"]["title"] == "Описание не соответствует товару"
    assert body["blocking_reason"]["comment"]
    assert isinstance(body["field_reports"], list)
    assert len(body["field_reports"]) == 2
    assert body["field_reports"][0]["field_name"] == "description"
    assert body["field_reports"][0]["sku_id"] is None
    assert body["field_reports"][1]["sku_id"] == sku_id


async def test_service_key_mode_sees_any_sellers_product(
    client, product_repository, sku_repository
):
    # Moderation calls with X-Service-Key and sees any seller's product.
    product = await seed_product(
        product_repository, status=ProductStatus.MODERATED, seller_id=OTHER_SELLER_ID
    )
    await seed_sku(sku_repository, product_id=product.id)

    async with client as ac:
        response = await ac.get(f"/api/v1/products/{product.id}", headers=service_headers())

    assert response.status_code == 200
    assert response.json()["id"] == product.id


# --- unhappy path ---------------------------------------------------------


async def test_get_others_product_returns_404(client, product_repository):
    product = await seed_product(
        product_repository, status=ProductStatus.MODERATED, seller_id=OTHER_SELLER_ID
    )

    async with client as ac:
        response = await ac.get(
            f"/api/v1/products/{product.id}", headers=auth_headers(SELLER_ID)
        )

    # 404 (not 403): do not reveal that someone else's product exists.
    assert response.status_code == 404
    assert response.json() == {"code": "NOT_FOUND", "message": "Product not found"}


async def test_get_nonexistent_returns_404(client):
    async with client as ac:
        response = await ac.get(f"/api/v1/products/{uuid4()}", headers=auth_headers())

    assert response.status_code == 404
    assert response.json() == {"code": "NOT_FOUND", "message": "Product not found"}


async def test_get_invalid_uuid_returns_400(client):
    async with client as ac:
        response = await ac.get("/api/v1/products/not-a-uuid", headers=auth_headers())

    assert response.status_code == 400
    assert response.json() == {"code": "INVALID_REQUEST", "message": "id must be a valid UUID"}


async def test_get_requires_auth(client, product_repository):
    product = await seed_product(product_repository, status=ProductStatus.MODERATED)

    async with client as ac:
        response = await ac.get(f"/api/v1/products/{product.id}")

    assert response.status_code == 401


async def test_invalid_service_key_returns_401(client, product_repository):
    product = await seed_product(product_repository, status=ProductStatus.MODERATED)

    async with client as ac:
        response = await ac.get(
            f"/api/v1/products/{product.id}", headers={"X-Service-Key": "wrong-key"}
        )

    assert response.status_code == 401
