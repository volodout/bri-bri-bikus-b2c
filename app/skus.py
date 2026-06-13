from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol
from uuid import UUID, uuid4

from app.errors import Forbidden, InvalidRequest, NotFound, ServiceUnavailable
from app.products import (
    CharacteristicValue,
    ProductRepository,
    ProductStatus,
    _parse_characteristics,
    _required_string,
    _required_uuid,
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


@dataclass(frozen=True)
class ProductEvent:
    idempotency_key: str
    product_id: str
    seller_id: str
    event: str
    date: str

    def as_payload(self) -> dict[str, Any]:
        return {
            "idempotency_key": self.idempotency_key,
            "product_id": self.product_id,
            "seller_id": self.seller_id,
            "event": self.event,
            "date": self.date,
        }


class ModerationGateway(Protocol):
    async def publish_product_event(self, event: ProductEvent) -> None: ...


class SkuRepository(Protocol):
    async def create_sku(self, sku: Sku) -> Sku: ...

    async def list_skus(self, product_id: str) -> tuple[Sku, ...]: ...

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
    # Creating a SKU spans two aggregates: it reads/writes the product (status)
    # and writes the SKU itself, so the service coordinates two repositories.
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
            await self._moderation.publish_product_event(
                ProductEvent(
                    idempotency_key=str(uuid4()),
                    product_id=product.id,
                    seller_id=product.seller_id,
                    event=event_type,
                    date=_event_timestamp(),
                )
            )
        return created


class InMemorySkuRepository:
    def __init__(self) -> None:
        self._skus: dict[str, list[Sku]] = {}

    async def create_sku(self, sku: Sku) -> Sku:
        self._skus.setdefault(sku.product_id, []).append(sku)
        return sku

    async def list_skus(self, product_id: str) -> tuple[Sku, ...]:
        return tuple(self._skus.get(product_id, ()))

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
        return sku

    async def list_skus(self, product_id: str) -> tuple[Sku, ...]:
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            sku_rows = await connection.fetch(
                """
                SELECT id::text, product_id::text, name, price, cost_price, discount,
                       image, active_quantity, reserved_quantity
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
            )
            for row in sku_rows
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


class RecordingModerationGateway:
    """In-memory gateway used in tests; records every published event."""

    def __init__(self) -> None:
        self.events: list[ProductEvent] = []

    async def publish_product_event(self, event: ProductEvent) -> None:
        self.events.append(event)


class HttpModerationGateway:
    """Synchronous delivery: POST the event to Moderation in the request flow.

    See docs/adr/0002-sku-moderation-event-delivery.md for the trade-off.
    """

    def __init__(
        self,
        base_url: str,
        service_key: str,
        *,
        transport: Any = None,
        timeout: float = 5.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._service_key = service_key
        self._transport = transport
        self._timeout = timeout

    async def publish_product_event(self, event: ProductEvent) -> None:
        import httpx

        async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as client:
            try:
                response = await client.post(
                    f"{self._base_url}/api/v1/events/product",
                    headers={"X-Service-Key": self._service_key},
                    json=event.as_payload(),
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise ServiceUnavailable("Moderation service is unavailable") from exc


def parse_sku_create(payload: Any) -> SkuCreate:
    if not isinstance(payload, Mapping):
        raise InvalidRequest("Request body must be a JSON object")

    product_id = _required_uuid(payload, "product_id")
    name = _required_string(payload, "name", max_length=255)
    price = _required_positive_int(payload, "price")
    cost_price = _required_positive_int(payload, "cost_price")
    discount = _optional_non_negative_int(payload, "discount")
    image = _required_string(payload, "image", max_length=2048)
    characteristics = _parse_characteristics(payload.get("characteristics", []))

    return SkuCreate(
        product_id=product_id,
        name=name,
        price=price,
        cost_price=cost_price,
        discount=discount,
        image=image,
        characteristics=characteristics,
    )


def to_sku_response(sku: Sku) -> dict[str, Any]:
    return {
        "id": sku.id,
        "product_id": sku.product_id,
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


def _event_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
