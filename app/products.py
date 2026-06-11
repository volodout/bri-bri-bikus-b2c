from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
try:
    from enum import StrEnum
except ImportError:
    from enum import Enum

    class StrEnum(str, Enum):
        pass
from typing import Any, Iterable, Mapping, Protocol
from uuid import UUID, uuid4

from app.errors import InvalidRequest


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


class ProductRepository(Protocol):
    async def get_category(self, category_id: str) -> Category | None: ...

    async def create_product(self, product: Product) -> Product: ...

    async def get_product(self, product_id: str) -> Product | None: ...

    async def aclose(self) -> None: ...


class ProductService:
    def __init__(self, repository: ProductRepository) -> None:
        self._repository = repository

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
        return _product_from_rows(product_row, image_rows, characteristic_rows)

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
    if not isinstance(value, list) or not value:
        raise InvalidRequest("At least one image is required")

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


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized or "product"


def _serialize_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _product_from_rows(product_row: Any, image_rows: Iterable[Any], characteristic_rows: Iterable[Any]) -> Product:
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
    )


def _parse_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
