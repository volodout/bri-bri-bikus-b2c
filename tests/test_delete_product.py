from __future__ import annotations

import json
from uuid import uuid4

import httpx

from app.b2c_events import HttpProductDeletionGateway, ProductDeletedEvent
from app.products import ProductStatus
from tests.conftest import (
    OTHER_SELLER_ID,
    SELLER_ID,
    auth_headers,
    seed_product,
    seed_sku,
)


async def test_delete_sets_deleted_true(client, product_repository):
    product = await seed_product(product_repository, status=ProductStatus.MODERATED)

    async with client as ac:
        response = await ac.delete(f"/api/v1/products/{product.id}", headers=auth_headers())

    assert response.status_code == 204
    assert response.content == b""
    deleted = await product_repository.get_product(product.id)
    assert deleted.deleted is True


async def test_delete_emits_event_to_moderation(
    client, product_repository, moderation_gateway
):
    product = await seed_product(product_repository, status=ProductStatus.MODERATED)

    async with client as ac:
        response = await ac.delete(f"/api/v1/products/{product.id}", headers=auth_headers())

    assert response.status_code == 204
    assert len(moderation_gateway.events) == 1
    event = moderation_gateway.events[0]
    assert event.event == "DELETED"
    assert event.product_id == product.id
    assert event.seller_id == SELLER_ID
    assert event.json_after["deleted"] is True


async def test_delete_emits_product_deleted_to_b2c(
    client, product_repository, sku_repository, product_deletion_gateway
):
    product = await seed_product(product_repository, status=ProductStatus.MODERATED)
    sku1 = await seed_sku(sku_repository, product_id=product.id)
    sku2 = await seed_sku(sku_repository, product_id=product.id)

    async with client as ac:
        response = await ac.delete(f"/api/v1/products/{product.id}", headers=auth_headers())

    assert response.status_code == 204
    assert len(product_deletion_gateway.events) == 1
    event = product_deletion_gateway.events[0]
    assert event.event == "PRODUCT_DELETED"
    assert event.product_id == product.id
    assert event.sku_ids == (sku1.id, sku2.id)


async def test_delete_already_deleted_returns_400(client, product_repository):
    product = await seed_product(
        product_repository,
        status=ProductStatus.MODERATED,
        deleted=True,
    )

    async with client as ac:
        response = await ac.delete(f"/api/v1/products/{product.id}", headers=auth_headers())

    assert response.status_code == 400
    assert response.json() == {"code": "INVALID_REQUEST", "message": "Product already deleted"}


async def test_delete_others_product_returns_403(client, product_repository):
    product = await seed_product(
        product_repository,
        status=ProductStatus.MODERATED,
        seller_id=OTHER_SELLER_ID,
    )

    async with client as ac:
        response = await ac.delete(
            f"/api/v1/products/{product.id}",
            headers=auth_headers(SELLER_ID),
        )

    assert response.status_code == 403
    assert response.json() == {
        "code": "NOT_OWNER",
        "message": "Product does not belong to the authenticated seller",
    }
    assert (await product_repository.get_product(product.id)).deleted is False


async def test_deleted_product_not_in_seller_list(client, product_repository):
    deleted = await seed_product(product_repository, status=ProductStatus.MODERATED)
    visible = await seed_product(
        product_repository,
        status=ProductStatus.CREATED,
        title="Visible product",
    )

    async with client as ac:
        removed = await ac.delete(f"/api/v1/products/{deleted.id}", headers=auth_headers())
        listed = await ac.get("/api/v1/products", headers=auth_headers())

    assert removed.status_code == 204
    assert listed.status_code == 200
    ids = [item["id"] for item in listed.json()["items"]]
    assert ids == [visible.id]
    assert deleted.id not in ids


async def test_product_deleted_event_conforms_to_b2c_flow():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["service_key"] = request.headers.get("X-Service-Key")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"accepted": True})

    gateway = HttpProductDeletionGateway(
        "http://b2c.test",
        "b2c-key",
        transport=httpx.MockTransport(handler),
    )
    await gateway.publish_product_deleted(
        ProductDeletedEvent(
            idempotency_key="11111111-1111-1111-1111-111111111111",
            product_id="22222222-2222-2222-2222-222222222222",
            sku_ids=("33333333-3333-3333-3333-333333333333",),
            date="2026-03-16T09:00:00.000Z",
        )
    )

    assert captured["url"] == "http://b2c.test/api/v1/b2b/events"
    assert captured["service_key"] == "b2c-key"
    assert captured["body"] == {
        "idempotency_key": "11111111-1111-1111-1111-111111111111",
        "event_type": "PRODUCT_DELETED",
        "payload": {
            "product_id": "22222222-2222-2222-2222-222222222222",
        },
        "occurred_at": "2026-03-16T09:00:00.000Z",
    }
