from __future__ import annotations

from typing import Any, Sequence

from app.errors import InvalidRequest
from app.products import (
    Product,
    ProductRepository,
    ProductStatus,
    _is_uuid,
    _serialize_datetime,
)
from app.skus import Sku, SkuRepository

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
_SORTS = {"price_asc", "price_desc", "created_desc"}


class CatalogService:
    """Public B2C catalog (US-B2B-07). Visibility: MODERATED, not deleted, and at
    least one SKU with active_quantity > 0. The short view never exposes
    cost_price / reserved_quantity."""

    def __init__(
        self, product_repository: ProductRepository, sku_repository: SkuRepository
    ) -> None:
        self._products = product_repository
        self._skus = sku_repository

    async def list_catalog(
        self,
        *,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
        category_id: str | None = None,
        search: str | None = None,
        sort: str | None = None,
        min_price: int | None = None,
        max_price: int | None = None,
        seller_id: str | None = None,
    ) -> dict[str, Any]:
        limit = _clamp_limit(limit)
        offset = max(0, offset)
        if category_id is not None and not _is_uuid(category_id):
            raise InvalidRequest("category_id must be a valid UUID")
        if seller_id is not None and not _is_uuid(seller_id):
            raise InvalidRequest("seller_id must be a valid UUID")
        if sort is not None and sort not in _SORTS:
            sort = None  # tolerate unsupported sorts (e.g. "popular") -> default order

        rows = await self._visible_rows()
        if category_id is not None:
            rows = [row for row in rows if row[0].category.id == category_id]
        if seller_id is not None:
            rows = [row for row in rows if row[0].seller_id == seller_id]
        if search:
            needle = search.lower()
            rows = [
                row
                for row in rows
                if needle in row[0].title.lower() or needle in row[0].description.lower()
            ]
        if min_price is not None:
            rows = [row for row in rows if row[1] >= min_price]
        if max_price is not None:
            rows = [row for row in rows if row[1] <= max_price]

        rows = _sort_rows(rows, sort)
        total = len(rows)
        page = rows[offset : offset + limit]
        return {
            "items": [_short(product, price) for product, price in page],
            "total_count": total,
            "limit": limit,
            "offset": offset,
        }

    async def batch(self, product_ids: Sequence[str]) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for product_id in product_ids:
            if product_id in seen:
                continue
            seen.add(product_id)
            product = await self._products.get_product(product_id)
            if product is None:
                continue
            skus = await self._skus.list_skus(product_id)
            if not _is_visible(product, skus):
                continue  # visibility applies: a hidden product is skipped, not 404
            items.append(_short(product, _min_price(skus)))
        return {"items": items}

    async def _visible_rows(self) -> list[tuple[Product, int]]:
        rows: list[tuple[Product, int]] = []
        for product in await self._products.list_products():
            skus = await self._skus.list_skus(product.id)
            if _is_visible(product, skus):
                rows.append((product, _min_price(skus)))
        return rows


def _is_visible(product: Product, skus: Sequence[Sku]) -> bool:
    return (
        product.status == ProductStatus.MODERATED
        and not product.deleted
        and any(sku.active_quantity > 0 for sku in skus)
    )


def _min_price(skus: Sequence[Sku]) -> int:
    return min((sku.price for sku in skus), default=0)


def _clamp_limit(limit: int) -> int:
    if limit < 1:
        return 1
    return min(limit, MAX_LIMIT)


def _sort_rows(rows: list[tuple[Product, int]], sort: str | None) -> list[tuple[Product, int]]:
    if sort == "price_asc":
        return sorted(rows, key=lambda row: row[1])
    if sort == "price_desc":
        return sorted(rows, key=lambda row: row[1], reverse=True)
    # default and "created_desc": newest first
    return sorted(rows, key=lambda row: row[0].created_at, reverse=True)


def _short(product: Product, min_price: int) -> dict[str, Any]:
    cover_image = None
    if product.images:
        cover_image = min(product.images, key=lambda image: image.ordering).url
    return {
        "id": product.id,
        "title": product.title,
        "slug": product.slug,
        "status": product.status.value,
        "category_id": product.category.id,
        "min_price": min_price,
        "cover_image": cover_image,
        "created_at": _serialize_datetime(product.created_at),
    }
