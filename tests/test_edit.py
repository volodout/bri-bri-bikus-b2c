from __future__ import annotations

from uuid import uuid4

from app.products import ProductStatus
from tests.conftest import (
    OTHER_SELLER_ID,
    SELLER_ID,
    auth_headers,
    seed_product,
    seed_sku,
    valid_product_update_payload,
    valid_sku_update_payload,
)


# --- editing a product: re-moderation -------------------------------------


async def test_edit_moderated_product_returns_to_on_moderation(
    client, product_repository, moderation_gateway
):
    product = await seed_product(product_repository, status=ProductStatus.MODERATED)

    async with client as ac:
        response = await ac.put(
            f"/api/v1/products/{product.id}",
            json=valid_product_update_payload(),
            headers=auth_headers(),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ON_MODERATION"
    assert body["title"] == "iPhone 15 Pro Max (обновлено)"
    assert (await product_repository.get_product(product.id)).status == ProductStatus.ON_MODERATION
    assert len(moderation_gateway.events) == 1
    assert moderation_gateway.events[0].event == "EDITED"


async def test_edit_blocked_product_returns_to_on_moderation(
    client, product_repository, moderation_gateway
):
    product = await seed_product(product_repository, status=ProductStatus.BLOCKED)

    async with client as ac:
        response = await ac.put(
            f"/api/v1/products/{product.id}",
            json=valid_product_update_payload(),
            headers=auth_headers(),
        )

    assert response.status_code == 200
    assert response.json()["status"] == "ON_MODERATION"
    assert (await product_repository.get_product(product.id)).status == ProductStatus.ON_MODERATION
    assert moderation_gateway.events[0].event == "EDITED"


async def test_edit_product_emits_edited_event_with_fields(
    client, product_repository, moderation_gateway
):
    product = await seed_product(product_repository, status=ProductStatus.MODERATED)

    async with client as ac:
        await ac.put(
            f"/api/v1/products/{product.id}",
            json=valid_product_update_payload(),
            headers=auth_headers(),
        )

    event = moderation_gateway.events[0]
    assert event.event == "EDITED"
    assert event.product_id == product.id
    assert event.seller_id == SELLER_ID
    assert event.idempotency_key


async def test_edit_created_product_stays_created(
    client, product_repository, moderation_gateway
):
    # A draft (CREATED) is not on the storefront yet: editing it must not
    # transition status or emit an event.
    product = await seed_product(product_repository, status=ProductStatus.CREATED)

    async with client as ac:
        response = await ac.put(
            f"/api/v1/products/{product.id}",
            json=valid_product_update_payload(),
            headers=auth_headers(),
        )

    assert response.status_code == 200
    assert response.json()["status"] == "CREATED"
    assert moderation_gateway.events == []


async def test_edit_on_moderation_product_emits_no_new_event(
    client, product_repository, moderation_gateway
):
    product = await seed_product(product_repository, status=ProductStatus.ON_MODERATION)

    async with client as ac:
        response = await ac.put(
            f"/api/v1/products/{product.id}",
            json=valid_product_update_payload(),
            headers=auth_headers(),
        )

    assert response.status_code == 200
    assert response.json()["status"] == "ON_MODERATION"
    assert moderation_gateway.events == []


# --- editing a SKU: reserves + parent re-moderation -----------------------


async def test_reserves_preserved_after_sku_edit(
    client, product_repository, sku_repository, moderation_gateway
):
    product = await seed_product(product_repository, status=ProductStatus.MODERATED)
    sku = await seed_sku(
        sku_repository, product_id=product.id, reserved_quantity=5, active_quantity=3
    )

    async with client as ac:
        response = await ac.put(
            f"/api/v1/skus/{sku.id}",
            json=valid_sku_update_payload(),
            headers=auth_headers(),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["reserved_quantity"] == 5  # reserves untouched by B2B
    assert body["active_quantity"] == 3
    assert body["name"] == "256GB Black Titanium"
    assert body["price"] == 13499000

    stored = await sku_repository.get_sku(sku.id)
    assert stored.reserved_quantity == 5
    assert stored.active_quantity == 3
    # Editing a SKU returns the parent product to moderation.
    assert (await product_repository.get_product(product.id)).status == ProductStatus.ON_MODERATION
    assert moderation_gateway.events[0].event == "EDITED"


async def test_edit_sku_returns_blocked_parent_to_on_moderation(
    client, product_repository, sku_repository, moderation_gateway
):
    product = await seed_product(product_repository, status=ProductStatus.BLOCKED)
    sku = await seed_sku(sku_repository, product_id=product.id)

    async with client as ac:
        response = await ac.put(
            f"/api/v1/skus/{sku.id}",
            json=valid_sku_update_payload(),
            headers=auth_headers(),
        )

    assert response.status_code == 200
    assert (await product_repository.get_product(product.id)).status == ProductStatus.ON_MODERATION
    assert moderation_gateway.events[0].event == "EDITED"
    assert moderation_gateway.events[0].product_id == product.id


# --- unhappy: ownership / hard-block / not found / validation -------------


async def test_edit_hard_blocked_returns_403(
    client, product_repository, moderation_gateway
):
    product = await seed_product(product_repository, status=ProductStatus.HARD_BLOCKED)

    async with client as ac:
        response = await ac.put(
            f"/api/v1/products/{product.id}",
            json=valid_product_update_payload(),
            headers=auth_headers(),
        )

    assert response.status_code == 403
    assert response.json() == {
        "code": "FORBIDDEN",
        "message": "Cannot edit hard-blocked product",
    }
    assert (await product_repository.get_product(product.id)).status == ProductStatus.HARD_BLOCKED
    assert moderation_gateway.events == []


async def test_edit_others_product_returns_403(
    client, product_repository, moderation_gateway
):
    product = await seed_product(
        product_repository, status=ProductStatus.MODERATED, seller_id=OTHER_SELLER_ID
    )

    async with client as ac:
        response = await ac.put(
            f"/api/v1/products/{product.id}",
            json=valid_product_update_payload(),
            headers=auth_headers(SELLER_ID),
        )

    assert response.status_code == 403
    assert response.json() == {
        "code": "NOT_OWNER",
        "message": "Product does not belong to the authenticated seller",
    }
    assert (await product_repository.get_product(product.id)).status == ProductStatus.MODERATED
    assert moderation_gateway.events == []


async def test_edit_ignores_seller_id_in_body(client, product_repository):
    # Ownership comes from the JWT only: spoofing the owner's id in the body
    # must not grant access to another seller's product.
    product = await seed_product(
        product_repository, status=ProductStatus.MODERATED, seller_id=OTHER_SELLER_ID
    )
    payload = valid_product_update_payload(seller_id=OTHER_SELLER_ID)

    async with client as ac:
        response = await ac.put(
            f"/api/v1/products/{product.id}",
            json=payload,
            headers=auth_headers(SELLER_ID),
        )

    assert response.status_code == 403
    assert response.json()["code"] == "NOT_OWNER"


async def test_edit_others_sku_returns_403(
    client, product_repository, sku_repository, moderation_gateway
):
    product = await seed_product(
        product_repository, status=ProductStatus.MODERATED, seller_id=OTHER_SELLER_ID
    )
    sku = await seed_sku(sku_repository, product_id=product.id)

    async with client as ac:
        response = await ac.put(
            f"/api/v1/skus/{sku.id}",
            json=valid_sku_update_payload(),
            headers=auth_headers(SELLER_ID),
        )

    assert response.status_code == 403
    assert response.json() == {
        "code": "NOT_OWNER",
        "message": "Product does not belong to the authenticated seller",
    }
    assert moderation_gateway.events == []


async def test_edit_sku_on_hard_blocked_parent_returns_403(
    client, product_repository, sku_repository, moderation_gateway
):
    product = await seed_product(product_repository, status=ProductStatus.HARD_BLOCKED)
    sku = await seed_sku(sku_repository, product_id=product.id)

    async with client as ac:
        response = await ac.put(
            f"/api/v1/skus/{sku.id}",
            json=valid_sku_update_payload(),
            headers=auth_headers(),
        )

    assert response.status_code == 403
    assert response.json() == {
        "code": "FORBIDDEN",
        "message": "Cannot edit hard-blocked product",
    }
    assert moderation_gateway.events == []


async def test_edit_product_not_found_returns_404(client):
    async with client as ac:
        response = await ac.put(
            f"/api/v1/products/{uuid4()}",
            json=valid_product_update_payload(),
            headers=auth_headers(),
        )

    assert response.status_code == 404
    assert response.json() == {"code": "NOT_FOUND", "message": "Product not found"}


async def test_edit_sku_not_found_returns_404(client):
    async with client as ac:
        response = await ac.put(
            f"/api/v1/skus/{uuid4()}",
            json=valid_sku_update_payload(),
            headers=auth_headers(),
        )

    assert response.status_code == 404
    assert response.json() == {"code": "NOT_FOUND", "message": "SKU not found"}


async def test_edit_product_invalid_data_returns_400(client, product_repository):
    product = await seed_product(product_repository, status=ProductStatus.MODERATED)
    payload = valid_product_update_payload()
    payload.pop("title")

    async with client as ac:
        response = await ac.put(
            f"/api/v1/products/{product.id}",
            json=payload,
            headers=auth_headers(),
        )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_REQUEST"


async def test_edit_sku_invalid_price_returns_400(client, product_repository, sku_repository):
    product = await seed_product(product_repository, status=ProductStatus.MODERATED)
    sku = await seed_sku(sku_repository, product_id=product.id)

    async with client as ac:
        response = await ac.put(
            f"/api/v1/skus/{sku.id}",
            json=valid_sku_update_payload(price=0),
            headers=auth_headers(),
        )

    assert response.status_code == 400
    assert response.json() == {
        "code": "INVALID_REQUEST",
        "message": "price must be a positive integer (kopecks)",
    }
