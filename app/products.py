from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
try:
    from enum import StrEnum
except ImportError:
    from enum import Enum

    class StrEnum(str, Enum):
        pass
from typing import Any, Iterable, Mapping, Protocol
from uuid import UUID, uuid4

from app.errors import Forbidden, InvalidRequest, NotFound, NotOwner
from app.moderation import ModerationGateway, ProductEvent, event_timestamp


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProductStatus(StrEnum):
    CREATED = "CREATED"
    ON_MODERATION = "ON_MODERATION"
    MODERATED = "MODERATED"
    BLOCKED = "BLOCKED"
    HARD_BLOCKED = "HARD_BLOCKED"


@dataclass(frozen=True)
class Category:
    id: str
    name: str


@dataclass(frozen=True)
class ProductImage:
    id: str
    url: str
    ordering: int


@dataclass(frozen=True)
class CharacteristicValue:
    id: str
    name: str
    value: str


@dataclass(frozen=True)
class ProductCreate:
    title: str
    description: str
    category_id: str
    images: tuple[ProductImage, ...]
    characteristics: tuple[CharacteristicValue, ...] = ()


@dataclass(frozen=True)
class BlockingReason:
    id: str
    title: str
    comment: str


@dataclass(frozen=True)
class FieldReport:
    field_name: str
    sku_id: str | None
    comment: str


@dataclass(frozen=True)
class Product:
    id: str
    seller_id: str
    category: Category
    title: str
    slug: str
    description: str
    status: ProductStatus
    deleted: bool
    images: tuple[ProductImage, ...]
    characteristics: tuple[CharacteristicValue, ...]
    skus: tuple[dict[str, Any], ...] = ()
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    blocking_reason: BlockingReason | None = None
    field_reports: tuple[FieldReport, ...] = ()


class ProductRepository(Protocol):
    async def get_category(self, category_id: str) -> Category | None: ...

    async def create_product(self, product: Product) -> Product: ...

    async def get_product(self, product_id: str) -> Product | None: ...

    async def list_products(self) -> tuple[Product, ...]: ...

    async def update_product(self, product: Product) -> Product: ...

    async def update_product_status(self, product_id: str, status: ProductStatus) -> None: ...

    async def update_moderation_state(
        self,
        product_id: str,
        status: ProductStatus,
        blocking_reason: "BlockingReason | None",
        field_reports: tuple["FieldReport", ...],
    ) -> None: ...

    async def aclose(self) -> None: ...


def ensure_owner(product: Product, seller_id: str) -> None:
    """IDOR guard: a seller may only edit their own product (or its SKUs).

    Centralised so every edit endpoint enforces ownership the same way and
    returns the canonical 403 NOT_OWNER (see docs/adr/0003-idor-protection.md).
    """
    if product.seller_id != seller_id:
        raise NotOwner("Product does not belong to the authenticated seller")


async def remoderate_on_edit(
    product: Product,
    repository: ProductRepository,
    moderation: ModerationGateway,
) -> None:
    """A significant edit of a checked product sends it back to moderation.

    MODERATED/BLOCKED -> ON_MODERATION + EDITED event. CREATED and ON_MODERATION
    are left untouched (a draft / already-queued product needs no transition).
    """
    if product.status in (ProductStatus.MODERATED, ProductStatus.BLOCKED):
        await repository.update_product_status(product.id, ProductStatus.ON_MODERATION)
        after = replace(product, status=ProductStatus.ON_MODERATION)
        await moderation.publish_product_event(
            ProductEvent(
                idempotency_key=str(uuid4()),
                product_id=product.id,
                seller_id=product.seller_id,
                event="EDITED",
                date=event_timestamp(),
                json_after=to_product_response(after),
                json_before=to_product_response(product),
                category_id=product.category.id,
            )
        )


class ProductService:
    def __init__(self, repository: ProductRepository, moderation: ModerationGateway) -> None:
        self._repository = repository
        self._moderation = moderation

    async def create_product(self, seller_id: str, payload: ProductCreate) -> Product:
        category = await self._repository.get_category(payload.category_id)
        if category is None:
            raise InvalidRequest("Category not found")

        now = _utcnow()
        product = Product(
            id=str(uuid4()),
            seller_id=seller_id,
            category=category,
            title=payload.title,
            slug=_slugify(payload.title),
            description=payload.description,
            status=ProductStatus.CREATED,
            deleted=False,
            images=payload.images,
            characteristics=payload.characteristics,
            skus=(),
            created_at=now,
            updated_at=now,
        )
        return await self._repository.create_product(product)

    async def update_product(self, seller_id: str, product_id: str, payload: ProductCreate) -> Product:
        if not _is_uuid(product_id):
            raise NotFound("Product not found")
        product = await self._repository.get_product(product_id)
        if product is None:
            raise NotFound("Product not found")
        ensure_owner(product, seller_id)
        if product.status == ProductStatus.HARD_BLOCKED:
            raise Forbidden("Cannot edit hard-blocked product")

        category = await self._repository.get_category(payload.category_id)
        if category is None:
            raise InvalidRequest("Category not found")

        updated = replace(
            product,
            category=category,
            title=payload.title,
            slug=_slugify(payload.title),
            description=payload.description,
            images=payload.images,
            characteristics=payload.characteristics,
            updated_at=_utcnow(),
        )
        await self._repository.update_product(updated)
        # The pre-edit status decides whether the card returns to moderation.
        await remoderate_on_edit(product, self._repository, self._moderation)
        return await self._repository.get_product(product_id)


class InMemoryProductRepository:
    def __init__(self, categories: Iterable[Category] | None = None) -> None:
        self._categories = {category.id: category for category in categories or ()}
        self._products: dict[str, Product] = {}

    def add_category(self, category: Category) -> None:
        self._categories[category.id] = category

    async def get_category(self, category_id: str) -> Category | None:
        return self._categories.get(category_id)

    async def create_product(self, product: Product) -> Product:
        self._products[product.id] = product
        return product

    async def get_product(self, product_id: str) -> Product | None:
        return self._products.get(product_id)

    async def list_products(self) -> tuple[Product, ...]:
        return tuple(self._products.values())

    async def update_product(self, product: Product) -> Product:
        self._products[product.id] = product
        return product

    async def update_product_status(self, product_id: str, status: ProductStatus) -> None:
        product = self._products.get(product_id)
        if product is not None:
            self._products[product_id] = replace(product, status=status, updated_at=_utcnow())

    async def update_moderation_state(
        self,
        product_id: str,
        status: ProductStatus,
        blocking_reason: BlockingReason | None,
        field_reports: tuple[FieldReport, ...],
    ) -> None:
        product = self._products.get(product_id)
        if product is not None:
            self._products[product_id] = replace(
                product,
                status=status,
                blocking_reason=blocking_reason,
                field_reports=tuple(field_reports),
                updated_at=_utcnow(),
            )

    async def aclose(self) -> None:
        return None


class PostgresProductRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._pool: Any = None

    async def get_category(self, category_id: str) -> Category | None:
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT id::text, name
                FROM categories
                WHERE id = $1 AND is_active = true
                """,
                UUID(category_id),
            )
        if row is None:
            return None
        return Category(id=str(row["id"]), name=row["name"])

    async def create_product(self, product: Product) -> Product:
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    INSERT INTO products (
                        id, seller_id, category_id, title, slug, description,
                        status, deleted, created_at, updated_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    """,
                    UUID(product.id),
                    UUID(product.seller_id),
                    UUID(product.category.id),
                    product.title,
                    product.slug,
                    product.description,
                    product.status.value,
                    product.deleted,
                    product.created_at,
                    product.updated_at,
                )
                for image in product.images:
                    await connection.execute(
                        """
                        INSERT INTO product_images (id, product_id, url, ordering)
                        VALUES ($1, $2, $3, $4)
                        """,
                        UUID(image.id),
                        UUID(product.id),
                        image.url,
                        image.ordering,
                    )
                for characteristic in product.characteristics:
                    await connection.execute(
                        """
                        INSERT INTO product_characteristics (id, product_id, name, value)
                        VALUES ($1, $2, $3, $4)
                        """,
                        UUID(characteristic.id),
                        UUID(product.id),
                        characteristic.name,
                        characteristic.value,
                    )
        return product

    async def get_product(self, product_id: str) -> Product | None:
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            product_row = await connection.fetchrow(
                """
                SELECT
                    p.id::text, p.seller_id::text, p.title, p.slug,
                    p.description, p.status, p.deleted, p.created_at, p.updated_at,
                    c.id::text AS category_id, c.name AS category_name
                FROM products p
                JOIN categories c ON c.id = p.category_id
                WHERE p.id = $1
                """,
                UUID(product_id),
            )
            if product_row is None:
                return None
            image_rows = await connection.fetch(
                """
                SELECT id::text, url, ordering
                FROM product_images
                WHERE product_id = $1
                ORDER BY ordering ASC, id ASC
                """,
                UUID(product_id),
            )
            characteristic_rows = await connection.fetch(
                """
                SELECT id::text, name, value
                FROM product_characteristics
                WHERE product_id = $1
                ORDER BY id ASC
                """,
                UUID(product_id),
            )
            blocking_reason_row = await connection.fetchrow(
                """
                SELECT reason_id::text, title, comment
                FROM product_blocking_reasons
                WHERE product_id = $1
                """,
                UUID(product_id),
            )
            field_report_rows = await connection.fetch(
                """
                SELECT field_name, sku_id::text, comment
                FROM product_field_reports
                WHERE product_id = $1
                ORDER BY id ASC
                """,
                UUID(product_id),
            )
        return _product_from_rows(
            product_row,
            image_rows,
            characteristic_rows,
            blocking_reason_row,
            field_report_rows,
        )

    async def list_products(self) -> tuple[Product, ...]:
        # Catalog short view only needs product + images; characteristics and
        # blocking data are omitted here for cheapness.
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            product_rows = await connection.fetch(
                """
                SELECT
                    p.id::text, p.seller_id::text, p.title, p.slug,
                    p.description, p.status, p.deleted, p.created_at, p.updated_at,
                    c.id::text AS category_id, c.name AS category_name
                FROM products p
                JOIN categories c ON c.id = p.category_id
                ORDER BY p.created_at DESC, p.id
                """,
            )
            image_rows = await connection.fetch(
                """
                SELECT id::text, product_id::text, url, ordering
                FROM product_images
                ORDER BY ordering ASC, id ASC
                """,
            )
        images_by_product: dict[str, list[ProductImage]] = {}
        for row in image_rows:
            images_by_product.setdefault(str(row["product_id"]), []).append(
                ProductImage(id=str(row["id"]), url=row["url"], ordering=int(row["ordering"]))
            )
        return tuple(
            Product(
                id=str(row["id"]),
                seller_id=str(row["seller_id"]),
                category=Category(id=str(row["category_id"]), name=row["category_name"]),
                title=row["title"],
                slug=row["slug"],
                description=row["description"],
                status=ProductStatus(row["status"]),
                deleted=bool(row["deleted"]),
                images=tuple(images_by_product.get(str(row["id"]), ())),
                characteristics=(),
                skus=(),
                created_at=_parse_datetime(row["created_at"]),
                updated_at=_parse_datetime(row["updated_at"]),
            )
            for row in product_rows
        )

    async def update_product(self, product: Product) -> Product:
        # Status is left untouched here; the re-moderation transition is applied
        # separately via update_product_status.
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    UPDATE products
                    SET category_id = $2, title = $3, slug = $4, description = $5,
                        updated_at = $6
                    WHERE id = $1
                    """,
                    UUID(product.id),
                    UUID(product.category.id),
                    product.title,
                    product.slug,
                    product.description,
                    product.updated_at,
                )
                await connection.execute(
                    "DELETE FROM product_images WHERE product_id = $1",
                    UUID(product.id),
                )
                for image in product.images:
                    await connection.execute(
                        """
                        INSERT INTO product_images (id, product_id, url, ordering)
                        VALUES ($1, $2, $3, $4)
                        """,
                        UUID(image.id),
                        UUID(product.id),
                        image.url,
                        image.ordering,
                    )
                await connection.execute(
                    "DELETE FROM product_characteristics WHERE product_id = $1",
                    UUID(product.id),
                )
                for characteristic in product.characteristics:
                    await connection.execute(
                        """
                        INSERT INTO product_characteristics (id, product_id, name, value)
                        VALUES ($1, $2, $3, $4)
                        """,
                        UUID(characteristic.id),
                        UUID(product.id),
                        characteristic.name,
                        characteristic.value,
                    )
        return product

    async def update_product_status(self, product_id: str, status: ProductStatus) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            await connection.execute(
                "UPDATE products SET status = $2, updated_at = now() WHERE id = $1",
                UUID(product_id),
                status.value,
            )

    async def update_moderation_state(
        self,
        product_id: str,
        status: ProductStatus,
        blocking_reason: BlockingReason | None,
        field_reports: tuple[FieldReport, ...],
    ) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    "UPDATE products SET status = $2, updated_at = now() WHERE id = $1",
                    UUID(product_id),
                    status.value,
                )
                await connection.execute(
                    "DELETE FROM product_blocking_reasons WHERE product_id = $1",
                    UUID(product_id),
                )
                if blocking_reason is not None:
                    await connection.execute(
                        """
                        INSERT INTO product_blocking_reasons (product_id, reason_id, title, comment)
                        VALUES ($1, $2, $3, $4)
                        """,
                        UUID(product_id),
                        UUID(blocking_reason.id),
                        blocking_reason.title,
                        blocking_reason.comment,
                    )
                await connection.execute(
                    "DELETE FROM product_field_reports WHERE product_id = $1",
                    UUID(product_id),
                )
                for report in field_reports:
                    await connection.execute(
                        """
                        INSERT INTO product_field_reports (id, product_id, field_name, sku_id, comment)
                        VALUES ($1, $2, $3, $4, $5)
                        """,
                        uuid4(),
                        UUID(product_id),
                        report.field_name,
                        UUID(report.sku_id) if report.sku_id else None,
                        report.comment,
                    )

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def _get_pool(self):
        if self._pool is None:
            import asyncpg

            self._pool = await asyncpg.create_pool(dsn=self._database_url)
        return self._pool


def parse_product_create(payload: Any) -> ProductCreate:
    if not isinstance(payload, Mapping):
        raise InvalidRequest("Request body must be a JSON object")

    title = _required_string(payload, "title", max_length=255)
    description = _required_string(payload, "description", max_length=5000)
    category_id = _required_uuid(payload, "category_id")
    images = _parse_images(payload.get("images"))
    characteristics = _parse_characteristics(payload.get("characteristics", []))

    return ProductCreate(
        title=title,
        description=description,
        category_id=category_id,
        images=images,
        characteristics=characteristics,
    )


def to_product_response(product: Product) -> dict[str, Any]:
    return {
        "id": product.id,
        "seller_id": product.seller_id,
        "category_id": product.category.id,
        "title": product.title,
        "slug": product.slug,
        "description": product.description,
        "status": product.status.value,
        "deleted": product.deleted,
        "blocked": product.status in {ProductStatus.BLOCKED, ProductStatus.HARD_BLOCKED},
        "blocking_reason_id": product.blocking_reason.id if product.blocking_reason else None,
        "moderator_comment": product.blocking_reason.comment if product.blocking_reason else None,
        "category": {"id": product.category.id, "name": product.category.name},
        "images": [
            {"id": image.id, "url": image.url, "ordering": image.ordering}
            for image in product.images
        ],
        "characteristics": [
            {"id": item.id, "name": item.name, "value": item.value}
            for item in product.characteristics
        ],
        "skus": list(product.skus),
        "created_at": _serialize_datetime(product.created_at),
        "updated_at": _serialize_datetime(product.updated_at),
    }


def _parse_images(value: Any) -> tuple[ProductImage, ...]:
    # images is optional per ProductCreate (default []) only validate when present.
    if value is None:
        return ()
    if not isinstance(value, list):
        raise InvalidRequest("images must be an array")

    images: list[ProductImage] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise InvalidRequest(f"images[{index}] must be an object")
        url = _required_string(raw, "url", max_length=2048, display_name=f"images[{index}].url")
        ordering = raw.get("ordering")
        if not isinstance(ordering, int) or ordering < 0:
            raise InvalidRequest(f"images[{index}].ordering must be a non-negative integer")
        images.append(ProductImage(id=str(uuid4()), url=url, ordering=ordering))
    return tuple(images)


def _parse_characteristics(value: Any) -> tuple[CharacteristicValue, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise InvalidRequest("characteristics must be an array")

    result: list[CharacteristicValue] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise InvalidRequest(f"characteristics[{index}] must be an object")
        name = _required_string(
            raw,
            "name",
            max_length=255,
            display_name=f"characteristics[{index}].name",
        )
        item_value = _required_string(
            raw,
            "value",
            max_length=2000,
            display_name=f"characteristics[{index}].value",
        )
        result.append(CharacteristicValue(id=str(uuid4()), name=name, value=item_value))
    return tuple(result)


def _required_string(
    payload: Mapping[str, Any],
    field_name: str,
    *,
    max_length: int,
    display_name: str | None = None,
) -> str:
    name = display_name or field_name
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise InvalidRequest(f"{name} is required")
    normalized = value.strip()
    if len(normalized) > max_length:
        raise InvalidRequest(f"{name} must be 1-{max_length} characters")
    return normalized


def _required_uuid(payload: Mapping[str, Any], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise InvalidRequest(f"{field_name} is required")
    try:
        return str(UUID(value))
    except ValueError:
        raise InvalidRequest(f"{field_name} must be a valid UUID")


def _is_uuid(value: Any) -> bool:
    try:
        UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized or "product"


def _serialize_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _product_from_rows(
    product_row: Any,
    image_rows: Iterable[Any],
    characteristic_rows: Iterable[Any],
    blocking_reason_row: Any = None,
    field_report_rows: Iterable[Any] = (),
) -> Product:
    blocking_reason = None
    if blocking_reason_row is not None:
        blocking_reason = BlockingReason(
            id=str(blocking_reason_row["reason_id"]),
            title=blocking_reason_row["title"],
            comment=blocking_reason_row["comment"],
        )
    return Product(
        id=str(product_row["id"]),
        seller_id=str(product_row["seller_id"]),
        category=Category(id=str(product_row["category_id"]), name=product_row["category_name"]),
        title=product_row["title"],
        slug=product_row["slug"],
        description=product_row["description"],
        status=ProductStatus(product_row["status"]),
        deleted=bool(product_row["deleted"]),
        images=tuple(
            ProductImage(id=str(row["id"]), url=row["url"], ordering=int(row["ordering"]))
            for row in image_rows
        ),
        characteristics=tuple(
            CharacteristicValue(id=str(row["id"]), name=row["name"], value=row["value"])
            for row in characteristic_rows
        ),
        skus=(),
        created_at=_parse_datetime(product_row["created_at"]),
        updated_at=_parse_datetime(product_row["updated_at"]),
        blocking_reason=blocking_reason,
        field_reports=tuple(
            FieldReport(
                field_name=row["field_name"],
                sku_id=str(row["sku_id"]) if row["sku_id"] is not None else None,
                comment=row["comment"],
            )
            for row in field_report_rows
        ),
    )


def _parse_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
