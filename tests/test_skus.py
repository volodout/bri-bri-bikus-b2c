from __future__ import annotations

import json
import re

import httpx

from app.moderation import HttpModerationGateway, ProductEvent
from app.products import ProductStatus
from tests.conftest import (
    SELLER_ID,
    auth_headers,
    seed_product,
    valid_sku_payload,
)

ISO_MILLIS_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


# --- happy path: status transition + events -------------------------------


async def test_first_sku_transitions_product_to_on_moderation(client, product_repository):
    product = await seed_product(product_repository, status=ProductStatus.CREATED)

    async with client as ac:
        response = await ac.post(
            "/api/v1/skus",
            json=valid_sku_payload(product_id=product.id),
            headers=auth_headers(),
        )

    assert response.status_code == 201
    updated = await product_repository.get_product(product.id)
    assert updated.status == ProductStatus.ON_MODERATION


async def test_first_sku_emits_created_event_to_moderation(
    client, product_repository, moderation_gateway
):
    product = await seed_product(product_repository, status=ProductStatus.CREATED)

    async with client as ac:
        response = await ac.post(
            "/api/v1/skus",
            json=valid_sku_payload(product_id=product.id),
            headers=auth_headers(),
        )

    assert response.status_code == 201
    assert len(moderation_gateway.events) == 1
    event = moderation_gateway.events[0]
    assert event.event == "CREATED"
    assert event.product_id == product.id
    assert event.seller_id == SELLER_ID
    assert event.idempotency_key  # present and non-empty
    assert ISO_MILLIS_Z.match(event.date)


async def test_second_sku_no_state_change(
    client, product_repository, sku_repository, moderation_gateway
):
    product = await seed_product(product_repository, status=ProductStatus.CREATED)

    async with client as ac:
        first = await ac.post(
            "/api/v1/skus",
            json=valid_sku_payload(product_id=product.id, name="256GB Black"),
            headers=auth_headers(),
        )
        second = await ac.post(
            "/api/v1/skus",
            json=valid_sku_payload(product_id=product.id, name="512GB White"),
            headers=auth_headers(),
        )

    assert first.status_code == 201
    assert second.status_code == 201
    # The second SKU is added while the product is already ON_MODERATION:
    # status stays put and no new event is emitted (still just the first one).
    updated = await product_repository.get_product(product.id)
    assert updated.status == ProductStatus.ON_MODERATION
    assert len(moderation_gateway.events) == 1
    assert len(await sku_repository.list_skus(product.id)) == 2


async def test_add_sku_to_moderated_emits_edited_event(
    client, product_repository, moderation_gateway
):
    # Re-moderation rule (Founder D-P8-01, 2026-05-27): adding a SKU to a
    # MODERATED product returns it to ON_MODERATION with an EDITED event.
    product = await seed_product(product_repository, status=ProductStatus.MODERATED)

    async with client as ac:
        response = await ac.post(
            "/api/v1/skus",
            json=valid_sku_payload(product_id=product.id),
            headers=auth_headers(),
        )

    assert response.status_code == 201
    updated = await product_repository.get_product(product.id)
    assert updated.status == ProductStatus.ON_MODERATION
    assert len(moderation_gateway.events) == 1
    assert moderation_gateway.events[0].event == "EDITED"


async def test_add_sku_to_blocked_emits_edited_event(
    client, product_repository, moderation_gateway
):
    product = await seed_product(product_repository, status=ProductStatus.BLOCKED)

    async with client as ac:
        response = await ac.post(
            "/api/v1/skus",
            json=valid_sku_payload(product_id=product.id),
            headers=auth_headers(),
        )

    assert response.status_code == 201
    updated = await product_repository.get_product(product.id)
    assert updated.status == ProductStatus.ON_MODERATION
    assert moderation_gateway.events[0].event == "EDITED"


async def test_sku_response_shape(client, product_repository):
    product = await seed_product(product_repository, status=ProductStatus.CREATED)

    async with client as ac:
        response = await ac.post(
            "/api/v1/skus",
            json=valid_sku_payload(product_id=product.id),
            headers=auth_headers(),
        )

    assert response.status_code == 201
    body = response.json()
    assert body["product_id"] == product.id
    assert body["name"] == "256GB Black"
    assert body["price"] == 12999000
    assert body["cost_price"] == 9500000
    assert body["discount"] == 0
    assert body["image"] == "/s3/iphone15-black-256.jpg"
    assert body["active_quantity"] == 0
    assert body["reserved_quantity"] == 0
    assert {"name": "Цвет", "value": "Чёрный"} in body["characteristics"]
    assert "id" in body


async def test_discount_defaults_to_zero(client, product_repository):
    product = await seed_product(product_repository, status=ProductStatus.CREATED)
    payload = valid_sku_payload(product_id=product.id)
    payload.pop("discount")

    async with client as ac:
        response = await ac.post("/api/v1/skus", json=payload, headers=auth_headers())

    assert response.status_code == 201
    assert response.json()["discount"] == 0


# --- unhappy path ---------------------------------------------------------


async def test_add_sku_to_hard_blocked_returns_403(
    client, product_repository, sku_repository, moderation_gateway
):
    product = await seed_product(product_repository, status=ProductStatus.HARD_BLOCKED)

    async with client as ac:
        response = await ac.post(
            "/api/v1/skus",
            json=valid_sku_payload(product_id=product.id),
            headers=auth_headers(),
        )

    assert response.status_code == 403
    assert response.json() == {
        "code": "FORBIDDEN",
        "message": "Cannot add SKU to hard-blocked product",
    }
    # No side effects: status untouched, no event, no SKU persisted.
    updated = await product_repository.get_product(product.id)
    assert updated.status == ProductStatus.HARD_BLOCKED
    assert moderation_gateway.events == []
    assert await sku_repository.list_skus(product.id) == ()


async def test_missing_image_returns_400(client, product_repository):
    product = await seed_product(product_repository, status=ProductStatus.CREATED)
    payload = valid_sku_payload(product_id=product.id)
    payload.pop("image")

    async with client as ac:
        response = await ac.post("/api/v1/skus", json=payload, headers=auth_headers())

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "INVALID_REQUEST"
    assert "image" in body["message"].lower()


async def test_product_not_found_returns_404(client):
    async with client as ac:
        response = await ac.post(
            "/api/v1/skus",
            json=valid_sku_payload(),  # product_id is a random, unseeded uuid
            headers=auth_headers(),
        )

    assert response.status_code == 404
    assert response.json() == {"code": "NOT_FOUND", "message": "Product not found"}


async def test_price_must_be_positive_returns_400(client, product_repository):
    product = await seed_product(product_repository, status=ProductStatus.CREATED)

    async with client as ac:
        response = await ac.post(
            "/api/v1/skus",
            json=valid_sku_payload(product_id=product.id, price=0),
            headers=auth_headers(),
        )

    assert response.status_code == 400
    assert response.json() == {
        "code": "INVALID_REQUEST",
        "message": "price must be a positive integer (kopecks)",
    }


async def test_cost_price_must_be_positive_returns_400(client, product_repository):
    product = await seed_product(product_repository, status=ProductStatus.CREATED)

    async with client as ac:
        response = await ac.post(
            "/api/v1/skus",
            json=valid_sku_payload(product_id=product.id, cost_price=-1),
            headers=auth_headers(),
        )

    assert response.status_code == 400
    assert response.json() == {
        "code": "INVALID_REQUEST",
        "message": "cost_price must be a positive integer (kopecks)",
    }


async def test_empty_name_returns_400(client, product_repository):
    product = await seed_product(product_repository, status=ProductStatus.CREATED)

    async with client as ac:
        response = await ac.post(
            "/api/v1/skus",
            json=valid_sku_payload(product_id=product.id, name="  "),
            headers=auth_headers(),
        )

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "INVALID_REQUEST"
    assert body["message"] == "name is required"


# --- transport contract: X-Service-Key + idempotency_key ------------------


async def test_moderation_gateway_sends_service_key_and_idempotency_key():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["service_key"] = request.headers.get("X-Service-Key")
        captured["body"] = json.loads(request.content)
        return httpx.Response(202, json={"status": "accepted"})

    gateway = HttpModerationGateway(
        "http://moderation.test",
        "secret-service-key",
        transport=httpx.MockTransport(handler),
    )
    event = ProductEvent(
        idempotency_key="d1e2f3a4-b5c6-7890-abcd-ef1234567890",
        product_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        seller_id="c3d4e5f6-a7b8-9012-cdef-123456789012",
        event="CREATED",
        date="2026-03-15T14:30:00.000Z",
        json_after={"id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "status": "ON_MODERATION"},
        category_id="f47ac10b-58cc-4372-a567-0e02b2c3d479",
    )

    await gateway.publish_product_event(event)

    # Conforms to Moderation's IncomingB2BEvent at POST /api/v1/b2b/events.
    assert captured["url"] == "http://moderation.test/api/v1/b2b/events"
    assert captured["service_key"] == "secret-service-key"
    body = captured["body"]
    assert body["event_type"] == "PRODUCT_CREATED"
    assert body["idempotency_key"] == "d1e2f3a4-b5c6-7890-abcd-ef1234567890"
    assert body["occurred_at"] == "2026-03-15T14:30:00.000Z"
    payload = body["payload"]
    assert payload["product_id"] == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    assert payload["seller_id"] == "c3d4e5f6-a7b8-9012-cdef-123456789012"
    assert payload["json_after"] == {
        "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "status": "ON_MODERATION",
    }
    assert "event" not in body  # old wire field is gone


def test_edited_event_payload_uses_product_edited_with_json_before():
    event = ProductEvent(
        idempotency_key="e2f3a4b5-c6d7-8901-bcde-f23456789012",
        product_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        seller_id="c3d4e5f6-a7b8-9012-cdef-123456789012",
        event="EDITED",
        date="2026-03-15T16:45:12.000Z",
        json_before={"status": "MODERATED"},
        json_after={"status": "ON_MODERATION"},
    )

    body = event.as_payload()

    assert body["event_type"] == "PRODUCT_EDITED"
    assert body["occurred_at"] == "2026-03-15T16:45:12.000Z"
    assert body["payload"]["json_before"] == {"status": "MODERATED"}
    assert body["payload"]["json_after"] == {"status": "ON_MODERATION"}
