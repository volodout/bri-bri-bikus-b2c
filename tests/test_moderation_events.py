from __future__ import annotations

import json
from uuid import uuid4

import httpx

from app.moderation_inbound import HttpB2CCatalogGateway, ProductBlockedEvent
from app.products import BlockingReason, FieldReport, ProductStatus
from tests.conftest import (
    auth_headers,
    seed_product,
    service_headers,
    valid_product_update_payload,
)


def moderation_event(product_id, event_type="MODERATED", **extra):
    body = {
        "idempotency_key": str(uuid4()),
        "product_id": product_id,
        "event_type": event_type,
        "occurred_at": "2026-03-15T14:30:00.000Z",
    }
    body.update(extra)
    return body


# --- happy path: the three decision paths ---------------------------------


async def test_moderated_event_clears_blocking_data(client, product_repository):
    product = await seed_product(
        product_repository,
        status=ProductStatus.ON_MODERATION,
        blocking_reason=BlockingReason(id=str(uuid4()), title="old", comment="old"),
        field_reports=(FieldReport(field_name="description", sku_id=None, comment="old"),),
    )

    async with client as ac:
        response = await ac.post(
            "/api/v1/moderation/events",
            json=moderation_event(product.id, "MODERATED"),
            headers=service_headers(),
        )

    assert response.status_code == 204
    updated = await product_repository.get_product(product.id)
    assert updated.status == ProductStatus.MODERATED
    assert updated.blocking_reason is None
    assert updated.field_reports == ()


async def test_blocked_soft_saves_field_reports(client, product_repository, b2c_catalog_gateway):
    product = await seed_product(product_repository, status=ProductStatus.ON_MODERATION)
    reason_id = str(uuid4())

    async with client as ac:
        response = await ac.post(
            "/api/v1/moderation/events",
            json=moderation_event(
                product.id,
                "BLOCKED",
                hard_block=False,
                blocking_reason_id=reason_id,
                moderator_comment="Несоответствие описания и фотографий",
                field_reports=[
                    {"field_name": "description", "sku_id": None, "comment": "Текст скопирован"}
                ],
            ),
            headers=service_headers(),
        )

    assert response.status_code == 204
    updated = await product_repository.get_product(product.id)
    assert updated.status == ProductStatus.BLOCKED
    assert updated.blocking_reason.id == reason_id
    assert updated.blocking_reason.comment == "Несоответствие описания и фотографий"
    assert len(updated.field_reports) == 1
    assert updated.field_reports[0].field_name == "description"
    # cascade to B2C
    assert len(b2c_catalog_gateway.events) == 1
    event = b2c_catalog_gateway.events[0]
    assert event.event_type == "PRODUCT_BLOCKED"
    assert event.product_id == product.id


async def test_blocked_hard_sets_terminal_status(client, product_repository, b2c_catalog_gateway):
    product = await seed_product(product_repository, status=ProductStatus.ON_MODERATION)

    async with client as ac:
        response = await ac.post(
            "/api/v1/moderation/events",
            json=moderation_event(
                product.id,
                "BLOCKED",
                hard_block=True,
                blocking_reason_id=str(uuid4()),
                moderator_comment="Контрафакт",
            ),
            headers=service_headers(),
        )

    assert response.status_code == 204
    updated = await product_repository.get_product(product.id)
    assert updated.status == ProductStatus.HARD_BLOCKED
    assert len(b2c_catalog_gateway.events) == 1
    # Hard block maps to the distinct B2C event type.
    assert b2c_catalog_gateway.events[0].event_type == "PRODUCT_HARD_BLOCKED"


async def test_hard_blocked_product_rejects_seller_edits(client, product_repository):
    product = await seed_product(product_repository, status=ProductStatus.ON_MODERATION)

    async with client as ac:
        applied = await ac.post(
            "/api/v1/moderation/events",
            json=moderation_event(
                product.id, "BLOCKED", hard_block=True, blocking_reason_id=str(uuid4())
            ),
            headers=service_headers(),
        )
        assert applied.status_code == 204
        edit = await ac.put(
            f"/api/v1/products/{product.id}",
            json=valid_product_update_payload(),
            headers=auth_headers(),
        )

    assert edit.status_code == 403
    assert edit.json() == {"code": "FORBIDDEN", "message": "Cannot edit hard-blocked product"}


# --- idempotency + auth ---------------------------------------------------


async def test_duplicate_event_same_idempotency_key_no_side_effects(
    client, product_repository, b2c_catalog_gateway
):
    product = await seed_product(product_repository, status=ProductStatus.ON_MODERATION)
    body = moderation_event(
        product.id, "BLOCKED", blocking_reason_id=str(uuid4()), moderator_comment="c"
    )

    async with client as ac:
        first = await ac.post("/api/v1/moderation/events", json=body, headers=service_headers())
        second = await ac.post("/api/v1/moderation/events", json=body, headers=service_headers())

    assert first.status_code == 204
    assert second.status_code == 204
    # The duplicate produced no extra cascade and left the state as the first set it.
    assert len(b2c_catalog_gateway.events) == 1
    assert (await product_repository.get_product(product.id)).status == ProductStatus.BLOCKED


async def test_missing_service_key_returns_401(client, product_repository):
    product = await seed_product(product_repository, status=ProductStatus.ON_MODERATION)

    async with client as ac:
        response = await ac.post(
            "/api/v1/moderation/events", json=moderation_event(product.id, "MODERATED")
        )

    assert response.status_code == 401
    # No state change without valid auth.
    assert (await product_repository.get_product(product.id)).status == ProductStatus.ON_MODERATION


# --- validation -----------------------------------------------------------


async def test_event_for_unknown_product_returns_404(client):
    async with client as ac:
        response = await ac.post(
            "/api/v1/moderation/events",
            json=moderation_event(str(uuid4()), "MODERATED"),
            headers=service_headers(),
        )

    assert response.status_code == 404
    assert response.json() == {"code": "NOT_FOUND", "message": "Product not found"}


async def test_blocked_without_blocking_reason_id_returns_400(client, product_repository):
    product = await seed_product(product_repository, status=ProductStatus.ON_MODERATION)

    async with client as ac:
        response = await ac.post(
            "/api/v1/moderation/events",
            json=moderation_event(product.id, "BLOCKED"),  # no blocking_reason_id
            headers=service_headers(),
        )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_REQUEST"


async def test_b2c_cascade_conforms_to_b2bevent():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["service_key"] = request.headers.get("X-Service-Key")
        captured["body"] = json.loads(request.content)
        return httpx.Response(202, json={"status": "accepted"})

    gateway = HttpB2CCatalogGateway(
        "http://b2c.test", "b2c-key", transport=httpx.MockTransport(handler)
    )
    await gateway.publish_product_blocked(
        ProductBlockedEvent(
            idempotency_key="11111111-1111-1111-1111-111111111111",
            event_type="PRODUCT_BLOCKED",
            occurred_at="2026-03-15T14:30:00.000Z",
            product_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            reason="bad photos",
        )
    )

    # Conforms to B2C's B2BEvent at POST /api/v1/b2b/events.
    assert captured["url"] == "http://b2c.test/api/v1/b2b/events"
    assert captured["service_key"] == "b2c-key"
    body = captured["body"]
    assert body["event_type"] == "PRODUCT_BLOCKED"
    assert body["occurred_at"] == "2026-03-15T14:30:00.000Z"
    assert body["idempotency_key"] == "11111111-1111-1111-1111-111111111111"
    assert body["payload"] == {
        "product_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "reason": "bad photos",
    }
    assert "event" not in body
    assert "sku_ids" not in body


async def test_invalid_event_type_returns_400(client, product_repository):
    product = await seed_product(product_repository, status=ProductStatus.ON_MODERATION)

    async with client as ac:
        response = await ac.post(
            "/api/v1/moderation/events",
            json=moderation_event(product.id, "WHATEVER"),
            headers=service_headers(),
        )

    assert response.status_code == 400
