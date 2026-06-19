from __future__ import annotations

from app.products import ProductStatus
from tests.conftest import (
    OTHER_SELLER_ID,
    SELLER_ID,
    auth_headers,
    seed_product,
    seed_sku,
)


def invoice_payload(*items):
    return {
        "items": [
            {"sku_id": sku_id, "quantity": quantity}
            for sku_id, quantity in items
        ]
    }


async def test_create_invoice_with_moderated_sku_returns_201(
    client, product_repository, sku_repository, invoice_repository
):
    product = await seed_product(product_repository, status=ProductStatus.MODERATED)
    sku = await seed_sku(sku_repository, product_id=product.id)

    async with client as ac:
        response = await ac.post(
            "/api/v1/invoices",
            json=invoice_payload((sku.id, 10)),
            headers=auth_headers(),
        )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "PENDING"
    assert body["created_at"]
    assert body["items"] == [
        {
            "sku_id": sku.id,
            "sku_name": sku.name,
            "quantity": 10,
            "accepted_quantity": None,
        }
    ]
    stored = await invoice_repository.get_invoice(body["id"])
    assert stored.status == "PENDING"
    assert stored.seller_id == SELLER_ID


async def test_empty_items_returns_400(client):
    async with client as ac:
        response = await ac.post(
            "/api/v1/invoices",
            json={"items": []},
            headers=auth_headers(),
        )

    assert response.status_code == 400
    assert response.json() == {
        "code": "INVALID_REQUEST",
        "message": "At least one item is required",
    }


async def test_non_moderated_sku_returns_400(client, product_repository, sku_repository):
    product = await seed_product(product_repository, status=ProductStatus.CREATED)
    sku = await seed_sku(sku_repository, product_id=product.id)

    async with client as ac:
        response = await ac.post(
            "/api/v1/invoices",
            json=invoice_payload((sku.id, 5)),
            headers=auth_headers(),
        )

    assert response.status_code == 400
    assert response.json() == {
        "code": "INVALID_REQUEST",
        "message": "Invoice can only be created for MODERATED products",
    }


async def test_others_sku_returns_403(client, product_repository, sku_repository):
    product = await seed_product(
        product_repository,
        status=ProductStatus.MODERATED,
        seller_id=OTHER_SELLER_ID,
    )
    sku = await seed_sku(sku_repository, product_id=product.id)

    async with client as ac:
        response = await ac.post(
            "/api/v1/invoices",
            json=invoice_payload((sku.id, 5)),
            headers=auth_headers(SELLER_ID),
        )

    assert response.status_code == 403
    assert response.json() == {
        "code": "NOT_OWNER",
        "message": "One or more SKUs do not belong to the authenticated seller",
    }
