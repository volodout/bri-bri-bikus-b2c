from __future__ import annotations

from uuid import uuid4

from app.products import ProductStatus
from tests.conftest import (
    CATEGORY_ID,
    SELLER_ID,
    auth_headers,
    b2c_service_headers,
    seed_product,
    seed_sku,
)


async def seed_visible(product_repository, sku_repository, *, price=12999000, title="iPhone 15", seller_id=SELLER_ID):
    product = await seed_product(
        product_repository, status=ProductStatus.MODERATED, seller_id=seller_id, title=title
    )
    await seed_sku(sku_repository, product_id=product.id, price=price, active_quantity=5)
    return product


# --- visibility -----------------------------------------------------------


async def test_catalog_returns_only_visible_products(client, product_repository, sku_repository):
    visible = await seed_visible(product_repository, sku_repository)

    created = await seed_product(product_repository, status=ProductStatus.CREATED)
    await seed_sku(sku_repository, product_id=created.id, active_quantity=5)

    deleted = await seed_product(product_repository, status=ProductStatus.MODERATED, deleted=True)
    await seed_sku(sku_repository, product_id=deleted.id, active_quantity=5)

    no_stock = await seed_product(product_repository, status=ProductStatus.MODERATED)
    await seed_sku(sku_repository, product_id=no_stock.id, active_quantity=0)

    async with client as ac:
        response = await ac.get("/api/v1/public/products", headers=b2c_service_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["total_count"] == 1
    assert [item["id"] for item in body["items"]] == [visible.id]


async def test_catalog_item_is_short_without_cost_fields(client, product_repository, sku_repository):
    await seed_visible(product_repository, sku_repository, price=899000)

    async with client as ac:
        response = await ac.get("/api/v1/public/products", headers=b2c_service_headers())

    item = response.json()["items"][0]
    assert set(item) == {
        "id",
        "title",
        "slug",
        "status",
        "category_id",
        "min_price",
        "cover_image",
        "created_at",
    }
    assert "skus" not in item
    assert "cost_price" not in item
    assert "reserved_quantity" not in item
    assert item["min_price"] == 899000
    assert item["category_id"] == CATEGORY_ID
    assert item["cover_image"] == "/s3/iphone15-front.jpg"


async def test_min_price_is_lowest_sku_price(client, product_repository, sku_repository):
    product = await seed_product(product_repository, status=ProductStatus.MODERATED)
    await seed_sku(sku_repository, product_id=product.id, price=500, active_quantity=2)
    await seed_sku(sku_repository, product_id=product.id, price=300, active_quantity=0)

    async with client as ac:
        response = await ac.get("/api/v1/public/products", headers=b2c_service_headers())

    assert response.json()["items"][0]["min_price"] == 300


# --- pagination / filters / sort ------------------------------------------


async def test_catalog_pagination(client, product_repository, sku_repository):
    for _ in range(3):
        await seed_visible(product_repository, sku_repository)

    async with client as ac:
        first = await ac.get(
            "/api/v1/public/products?limit=2&offset=0", headers=b2c_service_headers()
        )
        second = await ac.get(
            "/api/v1/public/products?limit=2&offset=2", headers=b2c_service_headers()
        )

    assert first.json()["total_count"] == 3
    assert len(first.json()["items"]) == 2
    assert first.json()["limit"] == 2
    assert len(second.json()["items"]) == 1


async def test_catalog_limit_capped_at_100(client, product_repository, sku_repository):
    await seed_visible(product_repository, sku_repository)

    async with client as ac:
        response = await ac.get("/api/v1/public/products?limit=500", headers=b2c_service_headers())

    assert response.json()["limit"] == 100


async def test_catalog_filter_by_category(client, product_repository, sku_repository):
    await seed_visible(product_repository, sku_repository)

    async with client as ac:
        match = await ac.get(
            f"/api/v1/public/products?category_id={CATEGORY_ID}", headers=b2c_service_headers()
        )
        other = await ac.get(
            f"/api/v1/public/products?category_id={uuid4()}", headers=b2c_service_headers()
        )

    assert match.json()["total_count"] == 1
    assert other.json()["total_count"] == 0


async def test_catalog_search_by_title(client, product_repository, sku_repository):
    await seed_visible(product_repository, sku_repository, title="iPhone 15 Pro")
    await seed_visible(product_repository, sku_repository, title="Galaxy S24")

    async with client as ac:
        response = await ac.get(
            "/api/v1/public/products?search=iphone", headers=b2c_service_headers()
        )

    body = response.json()
    assert body["total_count"] == 1
    assert "iPhone" in body["items"][0]["title"]


async def test_catalog_sort_price_asc_and_desc(client, product_repository, sku_repository):
    await seed_visible(product_repository, sku_repository, price=300)
    await seed_visible(product_repository, sku_repository, price=100)
    await seed_visible(product_repository, sku_repository, price=200)

    async with client as ac:
        asc = await ac.get("/api/v1/public/products?sort=price_asc", headers=b2c_service_headers())
        desc = await ac.get("/api/v1/public/products?sort=price_desc", headers=b2c_service_headers())

    assert [item["min_price"] for item in asc.json()["items"]] == [100, 200, 300]
    assert [item["min_price"] for item in desc.json()["items"]] == [300, 200, 100]


# --- batch ----------------------------------------------------------------


async def test_batch_returns_only_visible_by_ids(client, product_repository, sku_repository):
    visible = await seed_visible(product_repository, sku_repository)
    hidden = await seed_product(product_repository, status=ProductStatus.CREATED)
    await seed_sku(sku_repository, product_id=hidden.id, active_quantity=5)
    missing = str(uuid4())

    async with client as ac:
        response = await ac.post(
            "/api/v1/public/products/batch",
            json={"product_ids": [visible.id, hidden.id, missing]},
            headers=b2c_service_headers(),
        )

    assert response.status_code == 200
    body = response.json()
    # Bare array (not wrapped in {"items": ...}) per b2b.yaml.
    assert isinstance(body, list)
    # hidden (not MODERATED) and missing are skipped, not 404.
    assert [item["id"] for item in body] == [visible.id]


async def test_batch_item_is_full_public_product(client, product_repository, sku_repository):
    product = await seed_visible(product_repository, sku_repository, price=899000)

    async with client as ac:
        response = await ac.post(
            "/api/v1/public/products/batch",
            json={"product_ids": [product.id]},
            headers=b2c_service_headers(),
        )

    item = response.json()[0]
    # Full ProductPublicResponse shape.
    assert set(item) == {
        "id",
        "seller_id",
        "category_id",
        "title",
        "slug",
        "description",
        "status",
        "images",
        "characteristics",
        "skus",
        "created_at",
        "updated_at",
    }
    assert len(item["skus"]) == 1
    sku = item["skus"][0]
    # Public SKU: no cost_price / reserved_quantity; has stock_quantity / article.
    assert "cost_price" not in sku
    assert "reserved_quantity" not in sku
    assert sku["price"] == 899000
    assert sku["active_quantity"] == 5
    assert sku["stock_quantity"] == 5
    assert sku["article"] is None
    assert isinstance(sku["images"], list)


# --- auth -----------------------------------------------------------------


async def test_catalog_requires_service_key(client, product_repository, sku_repository):
    await seed_visible(product_repository, sku_repository)

    async with client as ac:
        missing = await ac.get("/api/v1/public/products")
        wrong = await ac.get("/api/v1/public/products", headers={"X-Service-Key": "nope"})

    assert missing.status_code == 401
    assert wrong.status_code == 401


async def test_catalog_not_accessible_via_bearer_without_service_key(
    client, product_repository, sku_repository
):
    # The catalog must not be reachable with a seller JWT alone — otherwise the
    # seller_id visibility scoping could be bypassed by swapping headers.
    await seed_visible(product_repository, sku_repository)

    async with client as ac:
        response = await ac.get("/api/v1/public/products", headers=auth_headers())

    assert response.status_code == 401
