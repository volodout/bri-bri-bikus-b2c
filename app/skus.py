from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Mapping, Protocol, Sequence
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from app.errors import Forbidden, InvalidRequest, NotFound
from app.moderation import ModerationGateway, ProductEvent, event_timestamp
from app.products import (
    CharacteristicValue,
    ProductRepository,
    ProductStatus,
    _is_uuid,
    _parse_characteristics,
    _required_string,
    _required_uuid,
    _serialize_datetime,
    _utcnow,
    ensure_owner,
    remoderate_on_edit,
    to_product_response,
)


@dataclass(frozen=True)
class SkuCreate:
    product_id: str
    name: str
    price: int
    cost_price: int
    discount: int
    image: str
    characteristics: tuple[CharacteristicValue, ...] = ()


@dataclass(frozen=True)
class SkuUpdate:
    name: str
    price: int
    cost_price: int
    discount: int
    image: str
    characteristics: tuple[CharacteristicValue, ...] = ()


@dataclass(frozen=True)
class Sku:
    id: str
    product_id: str
    name: str
    price: int
    cost_price: int
    discount: int
    image: str
    characteristics: tuple[CharacteristicValue, ...] = ()
    active_quantity: int = 0
    reserved_quantity: int = 0
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)


@dataclass(frozen=True)
class ReservedLine:
    sku_id: str
    reserved_quantity: int
    remaining_stock: int


@dataclass(frozen=True)
class ShortLine:
    sku_id: str
    requested: int
    available: int


@dataclass(frozen=True)
class ReserveOutcome:
    ok: bool
    reserved: tuple[ReservedLine, ...] = ()
    shortages: tuple[ShortLine, ...] = ()
    depleted: tuple[str, ...] = ()  # sku_ids whose active_quantity hit 0


# An item to reserve/unreserve: (sku_id, quantity).
ReserveLine = tuple[str, int]


class SkuRepository(Protocol):
    async def create_sku(self, sku: Sku) -> Sku: ...

    async def get_sku(self, sku_id: str) -> Sku | None: ...

    async def update_sku(self, sku: Sku) -> Sku: ...

    async def list_skus(self, product_id: str) -> tuple[Sku, ...]: ...

    async def reserve(self, items: Sequence[ReserveLine]) -> ReserveOutcome: ...

    async def unreserve(self, items: Sequence[ReserveLine]) -> None: ...

    async def aclose(self) -> None: ...


# Adding a SKU is a content change that must pass moderation before it can reach
# the B2C storefront, so the transition is driven by the product's current status,
# not by the number of SKUs it already has:
#   CREATED            -> first variant of a brand-new card  -> ON_MODERATION + CREATED
#   MODERATED / BLOCKED -> new unchecked variant on a live card -> ON_MODERATION + EDITED
#   ON_MODERATION      -> already queued (e.g. the second SKU) -> no transition, no event
#   HARD_BLOCKED       -> rejected up front with 403 (handled in the service)
def _transition_for(status: ProductStatus) -> tuple[ProductStatus, str] | None:
    if status == ProductStatus.CREATED:
        return ProductStatus.ON_MODERATION, "CREATED"
    if status in (ProductStatus.MODERATED, ProductStatus.BLOCKED):
        return ProductStatus.ON_MODERATION, "EDITED"
    return None


class SkuService:
    # Creating/editing a SKU spans two aggregates: it reads/writes the product
    # (status) and writes the SKU itself, so the service coordinates two repos.
    def __init__(
        self,
        product_repository: ProductRepository,
        sku_repository: SkuRepository,
        moderation: ModerationGateway,
    ) -> None:
        self._products = product_repository
        self._skus = sku_repository
        self._moderation = moderation

    async def create_sku(self, seller_id: str, payload: SkuCreate) -> Sku:
        product = await self._products.get_product(payload.product_id)
        if product is None:
            raise NotFound("Product not found")
        if product.status == ProductStatus.HARD_BLOCKED:
            raise Forbidden("Cannot add SKU to hard-blocked product")

        sku = Sku(
            id=str(uuid4()),
            product_id=product.id,
            name=payload.name,
            price=payload.price,
            cost_price=payload.cost_price,
            discount=payload.discount,
            image=payload.image,
            characteristics=payload.characteristics,
        )
        created = await self._skus.create_sku(sku)

        transition = _transition_for(product.status)
        if transition is not None:
            new_status, event_type = transition
            await self._products.update_product_status(product.id, new_status)
            after = replace(product, status=new_status)
            await self._moderation.publish_product_event(
                ProductEvent(
                    idempotency_key=str(uuid4()),
                    product_id=product.id,
                    seller_id=product.seller_id,
                    event=event_type,
                    date=event_timestamp(),
                    json_after=to_product_response(after),
                    json_before=to_product_response(product) if event_type == "EDITED" else None,
                    category_id=product.category.id,
                )
            )
        return created

    async def update_sku(self, seller_id: str, sku_id: str, payload: SkuUpdate) -> Sku:
        if not _is_uuid(sku_id):
            raise NotFound("SKU not found")
        sku = await self._skus.get_sku(sku_id)
        if sku is None:
            raise NotFound("SKU not found")

        product = await self._products.get_product(sku.product_id)
        if product is None:
            raise NotFound("Product not found")
        ensure_owner(product, seller_id)
        if product.status == ProductStatus.HARD_BLOCKED:
            raise Forbidden("Cannot edit hard-blocked product")

        # active_quantity / reserved_quantity are deliberately preserved: B2B does
        # not touch reserves on edit (see canon "Политика при активных резервах").
        updated = replace(
            sku,
            name=payload.name,
            price=payload.price,
            cost_price=payload.cost_price,
            discount=payload.discount,
            image=payload.image,
            characteristics=payload.characteristics,
        )
        saved = await self._skus.update_sku(updated)
        # Editing a SKU returns the parent product to moderation.
        await remoderate_on_edit(product, self._products, self._moderation)
        return saved


class InMemorySkuRepository:
    def __init__(self) -> None:
        self._skus: dict[str, Sku] = {}

    async def create_sku(self, sku: Sku) -> Sku:
        self._skus[sku.id] = sku
        return sku

    async def get_sku(self, sku_id: str) -> Sku | None:
        return self._skus.get(sku_id)

    async def update_sku(self, sku: Sku) -> Sku:
        self._skus[sku.id] = sku
        return sku

    async def list_skus(self, product_id: str) -> tuple[Sku, ...]:
        return tuple(sku for sku in self._skus.values() if sku.product_id == product_id)

    async def reserve(self, items: Sequence[ReserveLine]) -> ReserveOutcome:
        # Atomic within the coroutine: all checks and updates run with no await
        # in between, so no other reserve can interleave (single-threaded loop).
        shortages = [
            ShortLine(sku_id, quantity, _available(self._skus.get(sku_id)))
            for sku_id, quantity in items
            if _available(self._skus.get(sku_id)) < quantity
        ]
        if shortages:
            return ReserveOutcome(ok=False, shortages=tuple(shortages))

        reserved: list[ReservedLine] = []
        depleted: list[str] = []
        for sku_id, quantity in items:
            sku = self._skus[sku_id]
            updated = replace(
                sku,
                active_quantity=sku.active_quantity - quantity,
                reserved_quantity=sku.reserved_quantity + quantity,
            )
            self._skus[sku_id] = updated
            reserved.append(ReservedLine(sku_id, quantity, updated.active_quantity))
            if updated.active_quantity == 0:
                depleted.append(sku_id)
        return ReserveOutcome(ok=True, reserved=tuple(reserved), depleted=tuple(depleted))

    async def unreserve(self, items: Sequence[ReserveLine]) -> None:
        for sku_id, quantity in items:
            sku = self._skus.get(sku_id)
            if sku is not None:
                self._skus[sku_id] = replace(
                    sku,
                    active_quantity=sku.active_quantity + quantity,
                    reserved_quantity=sku.reserved_quantity - quantity,
                )

    async def aclose(self) -> None:
        return None


class PostgresSkuRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._pool: Any = None

    async def create_sku(self, sku: Sku) -> Sku:
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    INSERT INTO skus (
                        id, product_id, name, price, cost_price, discount, image,
                        active_quantity, reserved_quantity
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    """,
                    UUID(sku.id),
                    UUID(sku.product_id),
                    sku.name,
                    sku.price,
                    sku.cost_price,
                    sku.discount,
                    sku.image,
                    sku.active_quantity,
                    sku.reserved_quantity,
                )
                await self._insert_characteristics(connection, sku)
        return sku

    async def get_sku(self, sku_id: str) -> Sku | None:
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT id::text, product_id::text, name, price, cost_price, discount,
                       image, active_quantity, reserved_quantity, created_at, updated_at
                FROM skus
                WHERE id = $1
                """,
                UUID(sku_id),
            )
            if row is None:
                return None
            characteristic_rows = await connection.fetch(
                """
                SELECT id::text, name, value
                FROM sku_characteristics
                WHERE sku_id = $1
                ORDER BY id ASC
                """,
                UUID(sku_id),
            )
        return Sku(
            id=str(row["id"]),
            product_id=str(row["product_id"]),
            name=row["name"],
            price=int(row["price"]),
            cost_price=int(row["cost_price"]),
            discount=int(row["discount"]),
            image=row["image"],
            characteristics=tuple(
                CharacteristicValue(id=str(r["id"]), name=r["name"], value=r["value"])
                for r in characteristic_rows
            ),
            active_quantity=int(row["active_quantity"]),
            reserved_quantity=int(row["reserved_quantity"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def update_sku(self, sku: Sku) -> Sku:
        # active_quantity / reserved_quantity are intentionally not in the UPDATE,
        # so existing reserves survive the edit.
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    UPDATE skus
                    SET name = $2, price = $3, cost_price = $4, discount = $5,
                        image = $6, updated_at = now()
                    WHERE id = $1
                    """,
                    UUID(sku.id),
                    sku.name,
                    sku.price,
                    sku.cost_price,
                    sku.discount,
                    sku.image,
                )
                await connection.execute(
                    "DELETE FROM sku_characteristics WHERE sku_id = $1",
                    UUID(sku.id),
                )
                await self._insert_characteristics(connection, sku)
        return sku

    async def list_skus(self, product_id: str) -> tuple[Sku, ...]:
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            sku_rows = await connection.fetch(
                """
                SELECT id::text, product_id::text, name, price, cost_price, discount,
                       image, active_quantity, reserved_quantity, created_at, updated_at
                FROM skus
                WHERE product_id = $1
                ORDER BY created_at ASC, id ASC
                """,
                UUID(product_id),
            )
            characteristic_rows = await connection.fetch(
                """
                SELECT sku_id::text, id::text, name, value
                FROM sku_characteristics
                WHERE sku_id IN (
                    SELECT id FROM skus WHERE product_id = $1
                )
                ORDER BY id ASC
                """,
                UUID(product_id),
            )
        characteristics_by_sku: dict[str, list[CharacteristicValue]] = {}
        for row in characteristic_rows:
            characteristics_by_sku.setdefault(str(row["sku_id"]), []).append(
                CharacteristicValue(id=str(row["id"]), name=row["name"], value=row["value"])
            )
        return tuple(
            Sku(
                id=str(row["id"]),
                product_id=str(row["product_id"]),
                name=row["name"],
                price=int(row["price"]),
                cost_price=int(row["cost_price"]),
                discount=int(row["discount"]),
                image=row["image"],
                characteristics=tuple(characteristics_by_sku.get(str(row["id"]), ())),
                active_quantity=int(row["active_quantity"]),
                reserved_quantity=int(row["reserved_quantity"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in sku_rows
        )

    async def reserve(self, items: Sequence[ReserveLine]) -> ReserveOutcome:
        wanted = {sku_id: quantity for sku_id, quantity in items}
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            async with connection.transaction():
                rows = await connection.fetch(
                    # ORDER BY id locks the rows in a deterministic order, so two
                    # concurrent reserves over overlapping SKUs cannot deadlock.
                    """
                    SELECT id::text, active_quantity
                    FROM skus
                    WHERE id = ANY($1::uuid[])
                    ORDER BY id
                    FOR UPDATE
                    """,
                    [UUID(sku_id) for sku_id in wanted],
                )
                available = {str(row["id"]): int(row["active_quantity"]) for row in rows}
                shortages = [
                    ShortLine(sku_id, quantity, available.get(sku_id, 0))
                    for sku_id, quantity in wanted.items()
                    if available.get(sku_id, 0) < quantity
                ]
                if shortages:
                    # Nothing was modified (SELECT only), so all-or-nothing holds.
                    return ReserveOutcome(ok=False, shortages=tuple(shortages))
                reserved: list[ReservedLine] = []
                depleted: list[str] = []
                for sku_id, quantity in wanted.items():
                    new_active = available[sku_id] - quantity
                    await connection.execute(
                        """
                        UPDATE skus SET
                            active_quantity = active_quantity - $2,
                            reserved_quantity = reserved_quantity + $2,
                            updated_at = now()
                        WHERE id = $1
                        """,
                        UUID(sku_id),
                        quantity,
                    )
                    reserved.append(ReservedLine(sku_id, quantity, new_active))
                    if new_active == 0:
                        depleted.append(sku_id)
        return ReserveOutcome(ok=True, reserved=tuple(reserved), depleted=tuple(depleted))

    async def unreserve(self, items: Sequence[ReserveLine]) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            async with connection.transaction():
                for sku_id, quantity in items:
                    await connection.execute(
                        """
                        UPDATE skus SET
                            active_quantity = active_quantity + $2,
                            reserved_quantity = reserved_quantity - $2,
                            updated_at = now()
                        WHERE id = $1
                        """,
                        UUID(sku_id),
                        quantity,
                    )

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @staticmethod
    async def _insert_characteristics(connection: Any, sku: Sku) -> None:
        for characteristic in sku.characteristics:
            await connection.execute(
                """
                INSERT INTO sku_characteristics (id, sku_id, name, value)
                VALUES ($1, $2, $3, $4)
                """,
                UUID(characteristic.id),
                UUID(sku.id),
                characteristic.name,
                characteristic.value,
            )

    async def _get_pool(self):
        if self._pool is None:
            import asyncpg

            self._pool = await asyncpg.create_pool(dsn=self._database_url)
        return self._pool


def parse_sku_create(payload: Any) -> SkuCreate:
    if not isinstance(payload, Mapping):
        raise InvalidRequest("Request body must be a JSON object")

    product_id = _required_uuid(payload, "product_id")
    fields = _parse_sku_fields(payload)
    return SkuCreate(product_id=product_id, **fields)


def parse_sku_update(payload: Any) -> SkuUpdate:
    if not isinstance(payload, Mapping):
        raise InvalidRequest("Request body must be a JSON object")

    return SkuUpdate(**_parse_sku_fields(payload))


def to_sku_response(sku: Sku) -> dict[str, Any]:
    # SKUResponse per b2b.yaml.
    images: list[dict[str, Any]] = []
    if sku.image:
        # The model holds a single image URL; SKUImageResponse requires an id,
        # so derive a stable one from the SKU id.
        images = [{"id": str(uuid5(NAMESPACE_URL, f"{sku.id}:image")), "url": sku.image, "ordering": 0}]
    return {
        "id": sku.id,
        "product_id": sku.product_id,
        "name": sku.name,
        "price": sku.price,
        "cost_price": sku.cost_price,
        "discount": sku.discount,
        "stock_quantity": sku.active_quantity + sku.reserved_quantity,
        "active_quantity": sku.active_quantity,
        "reserved_quantity": sku.reserved_quantity,
        "article": None,
        "images": images,
        "characteristics": [
            {"name": item.name, "value": item.value} for item in sku.characteristics
        ],
        "created_at": _serialize_datetime(sku.created_at),
        "updated_at": _serialize_datetime(sku.updated_at),
    }


def _parse_sku_fields(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": _required_string(payload, "name", max_length=255),
        "price": _required_positive_int(payload, "price"),
        "cost_price": _required_positive_int(payload, "cost_price"),
        "discount": _optional_non_negative_int(payload, "discount"),
        "image": _primary_image_url(payload.get("images")),
        "characteristics": _parse_characteristics(payload.get("characteristics", [])),
    }


def _primary_image_url(value: Any) -> str:
    # images is optional per SKUCreate (default []); store the first URL.
    if value is None:
        return ""
    if not isinstance(value, list):
        raise InvalidRequest("images must be an array")
    if not value:
        return ""
    first = value[0]
    if not isinstance(first, Mapping):
        raise InvalidRequest("images[0] must be an object")
    return _required_string(first, "url", max_length=2048, display_name="images[0].url")


def _required_positive_int(payload: Mapping[str, Any], field_name: str) -> int:
    value = payload.get(field_name)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise InvalidRequest(f"{field_name} must be a positive integer (kopecks)")
    return value


def _optional_non_negative_int(payload: Mapping[str, Any], field_name: str, default: int = 0) -> int:
    value = payload.get(field_name)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidRequest(f"{field_name} must be a non-negative integer (kopecks)")
    return value


def _available(sku: Sku | None) -> int:
    return sku.active_quantity if sku is not None else 0
