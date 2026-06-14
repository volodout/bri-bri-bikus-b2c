from __future__ import annotations

from tests.conftest import (
    CATEGORY_ID,
    OTHER_SELLER_ID,
    SELLER_ID,
    UNKNOWN_CATEGORY_ID,
    auth_headers,
)


def valid_product_payload(**overrides):
    payload = {
        "title": "iPhone 15 Pro Max",
        "description": "Flagship Apple smartphone with A17 Pro chip",
        "category_id": CATEGORY_ID,
        "seller_id": OTHER_SELLER_ID,
        "images": [
            {"url": "/s3/iphone15-front.jpg", "ordering": 0},
            {"url": "/s3/iphone15-back.jpg", "ordering": 1},
        ],
        "characteristics": [
            {"name": "Brand", "value": "Apple"},
            {"name": "Country", "value": "China"},
        ],
    }
    payload.update(overrides)
    return payload


async def test_create_product_returns_201_with_created_status(client):
    async with client as ac:
        response = await ac.post(
            "/api/v1/products",
            json=valid_product_payload(),
            headers=auth_headers(),
        )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "CREATED"
    assert body["deleted"] is False
    assert body["blocked"] is False
    assert body["skus"] == []
    assert body["title"] == "iPhone 15 Pro Max"
    assert body["category"]["id"] == CATEGORY_ID
    assert body["images"][0]["url"] == "/s3/iphone15-front.jpg"
    # ProductResponse required, nullable on a fresh product.
    assert body["blocking_reason_id"] is None
    assert body["moderator_comment"] is None


async def test_seller_id_taken_from_jwt(client):
    async with client as ac:
        response = await ac.post(
            "/api/v1/products",
            json=valid_product_payload(seller_id=OTHER_SELLER_ID),
            headers=auth_headers(SELLER_ID),
        )

    assert response.status_code == 201
    body = response.json()
    assert body["seller_id"] == SELLER_ID
    assert body["seller_id"] != OTHER_SELLER_ID


async def test_missing_images_defaults_to_empty(client):
    # images is optional per ProductCreate (default []): a product can be created
    # without images and the response carries an empty list, not a 400.
    payload = valid_product_payload()
    payload.pop("images")

    async with client as ac:
        response = await ac.post(
            "/api/v1/products",
            json=payload,
            headers=auth_headers(),
        )

    assert response.status_code == 201
    assert response.json()["images"] == []


async def test_missing_category_returns_400(client):
    payload = valid_product_payload()
    payload.pop("category_id")

    async with client as ac:
        response = await ac.post(
            "/api/v1/products",
            json=payload,
            headers=auth_headers(),
        )

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "INVALID_REQUEST"
    assert "category_id" in body["message"]


async def test_invalid_category_id_returns_400(client):
    async with client as ac:
        response = await ac.post(
            "/api/v1/products",
            json=valid_product_payload(category_id=UNKNOWN_CATEGORY_ID),
            headers=auth_headers(),
        )

    assert response.status_code == 400
    assert response.json() == {"code": "INVALID_REQUEST", "message": "Category not found"}


async def test_category_id_must_be_uuid_returns_400(client):
    async with client as ac:
        response = await ac.post(
            "/api/v1/products",
            json=valid_product_payload(category_id="not-a-uuid"),
            headers=auth_headers(),
        )

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "INVALID_REQUEST"
    assert body["message"] == "category_id must be a valid UUID"
