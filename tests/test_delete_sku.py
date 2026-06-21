from __future__ import annotations

from app.products import ProductStatus
from tests.conftest import (
    auth_headers,
    seed_product,
    seed_sku,
)


async def test_delete_sku_succeeds(client, product_repository, sku_repository):
    product = await seed_product(product_repository, status=ProductStatus.MODERATED)
    sku = await seed_sku(sku_repository, product_id=product.id)

    async with client as ac:
        response = await ac.delete(f"/api/v1/skus/{sku.id}", headers=auth_headers())

    assert response.status_code == 204
    assert await sku_repository.get_sku(sku.id) is None


async def test_delete_sku_with_active_reserves_returns_409(
    client, product_repository, sku_repository
):
    product = await seed_product(product_repository, status=ProductStatus.MODERATED)
    sku = await seed_sku(sku_repository, product_id=product.id, reserved_quantity=2)

    async with client as ac:
        response = await ac.delete(f"/api/v1/skus/{sku.id}", headers=auth_headers())

    assert response.status_code == 409
    assert response.json()["code"] == "CONFLICT"
    assert await sku_repository.get_sku(sku.id) is not None


async def test_last_sku_on_moderation_transitions_product_to_created(
    client, product_repository, sku_repository, moderation_gateway
):
    product = await seed_product(product_repository, status=ProductStatus.ON_MODERATION)
    sku = await seed_sku(sku_repository, product_id=product.id)

    async with client as ac:
        response = await ac.delete(f"/api/v1/skus/{sku.id}", headers=auth_headers())

    assert response.status_code == 204
    updated = await product_repository.get_product(product.id)
    assert updated.status == ProductStatus.CREATED
    assert len(moderation_gateway.events) == 1
    event = moderation_gateway.events[0]
    assert event.event == "DELETED"
    assert event.product_id == product.id


async def test_delete_sku_hard_blocked_product_returns_403(
    client, product_repository, sku_repository
):
    product = await seed_product(product_repository, status=ProductStatus.HARD_BLOCKED)
    sku = await seed_sku(sku_repository, product_id=product.id)

    async with client as ac:
        response = await ac.delete(f"/api/v1/skus/{sku.id}", headers=auth_headers())

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"
    assert await sku_repository.get_sku(sku.id) is not None


async def test_sku_out_of_stock_event_on_moderated_product(
    client, product_repository, sku_repository, b2c_gateway
):
    product = await seed_product(product_repository, status=ProductStatus.MODERATED)
    sku = await seed_sku(sku_repository, product_id=product.id, active_quantity=5)

    async with client as ac:
        response = await ac.delete(f"/api/v1/skus/{sku.id}", headers=auth_headers())

    assert response.status_code == 204
    assert len(b2c_gateway.events) == 1
    event = b2c_gateway.events[0]
    assert event.event == "SKU_OUT_OF_STOCK"
    assert event.sku_id == sku.id
    assert event.product_id == product.id
    assert event.available_quantity == 0
