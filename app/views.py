from __future__ import annotations

from typing import Any

from app.errors import InvalidRequest, NotFound
from app.products import (
    BlockingReason,
    FieldReport,
    Product,
    ProductRepository,
    ProductStatus,
    _is_uuid,
)
from app.skus import Sku, SkuRepository


class ProductViewService:
    """Read side of the product card (US-B2B-05): assembles a product with its
    SKUs and moderation feedback for the seller cabinet and for Moderation."""

    def __init__(
        self, product_repository: ProductRepository, sku_repository: SkuRepository
    ) -> None:
        self._products = product_repository
        self._skus = sku_repository

    async def get_product_view(
        self, product_id: str, *, seller_id: str | None
    ) -> tuple[Product, tuple[Sku, ...]]:
        if not _is_uuid(product_id):
            raise InvalidRequest("id must be a valid UUID")
        product = await self._products.get_product(product_id)
        if product is None:
            raise NotFound("Product not found")
        # seller_id is None in trusted service mode (Moderation) -> skip ownership.
        # In seller mode a foreign product is reported as 404 (never 403), so the
        # existence of someone else's product is not revealed.
        if seller_id is not None and product.seller_id != seller_id:
            raise NotFound("Product not found")
        skus = await self._skus.list_skus(product_id)
        return product, skus


def to_product_view(product: Product, skus: tuple[Sku, ...]) -> dict[str, Any]:
    return {
        "id": product.id,
        "title": product.title,
        "description": product.description,
        "status": product.status.value,
        "deleted": product.deleted,
        "blocked": product.status in {ProductStatus.BLOCKED, ProductStatus.HARD_BLOCKED},
        "category": {"id": product.category.id, "name": product.category.name},
        "images": [
            {"url": image.url, "ordering": image.ordering} for image in product.images
        ],
        "characteristics": [
            {"name": item.name, "value": item.value} for item in product.characteristics
        ],
        "skus": [_sku_view(sku) for sku in skus],
        "blocking_reason": _blocking_reason_view(product.blocking_reason),
        "field_reports": [_field_report_view(report) for report in product.field_reports],
    }


def _sku_view(sku: Sku) -> dict[str, Any]:
    # Seller-cabinet view: cost_price and reserved_quantity are included here;
    # they are stripped only from the B2C catalog (see US-B2B-07).
    return {
        "id": sku.id,
        "name": sku.name,
        "price": sku.price,
        "cost_price": sku.cost_price,
        "discount": sku.discount,
        "image": sku.image,
        "active_quantity": sku.active_quantity,
        "reserved_quantity": sku.reserved_quantity,
        "characteristics": [
            {"name": item.name, "value": item.value} for item in sku.characteristics
        ],
    }


def _blocking_reason_view(reason: BlockingReason | None) -> dict[str, Any] | None:
    if reason is None:
        return None
    return {"id": reason.id, "title": reason.title, "comment": reason.comment}


def _field_report_view(report: FieldReport) -> dict[str, Any]:
    return {
        "field_name": report.field_name,
        "sku_id": report.sku_id,
        "comment": report.comment,
    }
