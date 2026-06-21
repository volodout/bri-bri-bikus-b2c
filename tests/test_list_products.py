from __future__ import annotations

from app.products import ProductStatus
from tests.conftest import (
    OTHER_SELLER_ID,
    SELLER_ID,
    auth_headers,
    seed_product,
)


async def test_list_returns_only_own_products(client, product_repository, sku_repository):
    own = await seed_product(product_repository, seller_id=SELLER_ID, title="A")
    await seed_product(product_repository, seller_id=OTHER_SELLER_ID, title="B")

    async with client as ac:
        response = await ac.get("/api/v1/products", headers=auth_headers(SELLER_ID))

    assert response.status_code == 200
    body = response.json()
    assert body["total_count"] == 1
    assert body["items"][0]["id"] == own.id
    assert all(item["id"] != OTHER_SELLER_ID for item in body["items"])


async def test_idor_query_param_seller_id_ignored(client, product_repository):
    own = await seed_product(product_repository, seller_id=SELLER_ID)
    await seed_product(product_repository, seller_id=OTHER_SELLER_ID)

    async with client as ac:
        response = await ac.get(
            "/api/v1/products",
            params={"seller_id": OTHER_SELLER_ID},
            headers=auth_headers(SELLER_ID),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["total_count"] == 1
    assert body["items"][0]["id"] == own.id


async def test_deleted_products_visible_with_deleted_flag(client, product_repository):
    active = await seed_product(product_repository, deleted=False, title="Active")
    deleted = await seed_product(product_repository, deleted=True, title="Deleted")

    async with client as ac:
        without_flag = await ac.get("/api/v1/products", headers=auth_headers())
        with_flag = await ac.get(
            "/api/v1/products",
            params={"include_deleted": "true"},
            headers=auth_headers(),
        )

    ids_without = {item["id"] for item in without_flag.json()["items"]}
    ids_with = {item["id"] for item in with_flag.json()["items"]}

    assert active.id in ids_without
    assert deleted.id not in ids_without
    assert active.id in ids_with
    assert deleted.id in ids_with


async def test_status_filter_works_correctly(client, product_repository):
    await seed_product(product_repository, status=ProductStatus.CREATED)
    await seed_product(product_repository, status=ProductStatus.BLOCKED)

    async with client as ac:
        response = await ac.get(
            "/api/v1/products",
            params={"status": "BLOCKED"},
            headers=auth_headers(),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["total_count"] == 1
    assert body["items"][0]["status"] == "BLOCKED"


async def test_search_by_title_case_insensitive(client, product_repository):
    await seed_product(product_repository, title="A")
    await seed_product(product_repository, title="B")

    async with client as ac:
        response = await ac.get(
            "/api/v1/products",
            params={"search": "a"},
            headers=auth_headers(),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["total_count"] == 1
    assert "A" in body["items"][0]["title"]
