from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol
from uuid import UUID, uuid4

from app.errors import InvalidRequest, ServiceUnavailable
from app.moderation import event_timestamp
from app.products import _required_uuid
from app.skus import SkuRepository


@dataclass(frozen=True)
class ReserveItem:
    sku_id: str
    quantity: int


@dataclass(frozen=True)
class ReserveRequest:
    idempotency_key: str
    order_id: str
    items: tuple[ReserveItem, ...]


@dataclass(frozen=True)
class UnreserveRequest:
    order_id: str
    items: tuple[ReserveItem, ...]


@dataclass
class ReserveResponse:
    reserved: bool
    order_id: str | None = None
    reserved_at: str | None = None
    failed_items: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class StockEvent:
    event: str  # internal kind, e.g. "SKU_OUT_OF_STOCK" -> B2BEvent.event_type
    sku_id: str
    product_id: str
    available_quantity: int
    idempotency_key: str = field(default_factory=lambda: str(uuid4()))
    occurred_at: str = field(default_factory=event_timestamp)

    def as_payload(self) -> dict[str, Any]:
        # B2C's B2BEvent: payload is EventSkuStock {sku_id, product_id, available_quantity}.
        return {
            "event_type": self.event,
            "idempotency_key": self.idempotency_key,
            "occurred_at": self.occurred_at,
            "payload": {
                "sku_id": self.sku_id,
                "product_id": self.product_id,
                "available_quantity": self.available_quantity,
            },
        }


class B2CGateway(Protocol):
    async def publish_stock_event(self, event: StockEvent) -> None: ...


class ReserveStore(Protocol):
    async def get_reserve(self, idempotency_key: str) -> dict[str, Any] | None: ...

    async def save_reserve(self, idempotency_key: str, result: dict[str, Any]) -> None: ...

    async def was_unreserved(self, order_id: str) -> bool: ...

    async def mark_unreserved(self, order_id: str) -> None: ...

    async def aclose(self) -> None: ...


class InventoryService:
    def __init__(
        self,
        sku_repository: SkuRepository,
        reserve_store: ReserveStore,
        b2c_gateway: B2CGateway,
    ) -> None:
        self._skus = sku_repository
        self._store = reserve_store
        self._b2c = b2c_gateway

    async def reserve(self, request: ReserveRequest) -> ReserveResponse:
        cached = await self._store.get_reserve(request.idempotency_key)
        if cached is not None:
            # Already processed successfully: replay the stored result, no re-deduction.
            return ReserveResponse(
                reserved=True,
                order_id=cached["order_id"],
                reserved_at=cached["reserved_at"],
            )

        outcome = await self._skus.reserve([(item.sku_id, item.quantity) for item in request.items])
        if not outcome.ok:
            failed = [
                {
                    "sku_id": short.sku_id,
                    "requested": short.requested,
                    "available": short.available,
                    "reason": "OUT_OF_STOCK" if short.available == 0 else "INSUFFICIENT_STOCK",
                }
                for short in outcome.shortages
            ]
            # 409 is not cached: a retry should re-check stock, which may have changed.
            return ReserveResponse(reserved=False, failed_items=failed)

        reserved_at = event_timestamp()
        await self._store.save_reserve(
            request.idempotency_key,
            {"order_id": request.order_id, "reserved_at": reserved_at},
        )
        for sku_id in outcome.depleted:
            sku = await self._skus.get_sku(sku_id)
            await self._b2c.publish_stock_event(
                StockEvent(
                    event="SKU_OUT_OF_STOCK",
                    sku_id=sku_id,
                    product_id=sku.product_id if sku else "",
                    available_quantity=sku.active_quantity if sku else 0,
                )
            )
        return ReserveResponse(reserved=True, order_id=request.order_id, reserved_at=reserved_at)

    async def unreserve(self, request: UnreserveRequest) -> str:
        # Idempotent on order_id: a retried cancellation does not double-restore.
        if not await self._store.was_unreserved(request.order_id):
            await self._skus.unreserve([(item.sku_id, item.quantity) for item in request.items])
            await self._store.mark_unreserved(request.order_id)
        return event_timestamp()


class RecordingB2CGateway:
    """In-memory B2C gateway used in tests; records every stock event."""

    def __init__(self) -> None:
        self.events: list[StockEvent] = []

    async def publish_stock_event(self, event: StockEvent) -> None:
        self.events.append(event)


class HttpB2CGateway:
    def __init__(
        self, base_url: str, service_key: str, *, transport: Any = None, timeout: float = 5.0
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._service_key = service_key
        self._transport = transport
        self._timeout = timeout

    async def publish_stock_event(self, event: StockEvent) -> None:
        import httpx

        async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as client:
            try:
                response = await client.post(
                    f"{self._base_url}/api/v1/b2b/events",
                    headers={"X-Service-Key": self._service_key},
                    json=event.as_payload(),
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise ServiceUnavailable("B2C service is unavailable") from exc


class InMemoryReserveStore:
    def __init__(self) -> None:
        self._reserves: dict[str, dict[str, Any]] = {}
        self._unreserves: set[str] = set()

    async def get_reserve(self, idempotency_key: str) -> dict[str, Any] | None:
        return self._reserves.get(idempotency_key)

    async def save_reserve(self, idempotency_key: str, result: dict[str, Any]) -> None:
        self._reserves.setdefault(idempotency_key, result)

    async def was_unreserved(self, order_id: str) -> bool:
        return order_id in self._unreserves

    async def mark_unreserved(self, order_id: str) -> None:
        self._unreserves.add(order_id)

    async def aclose(self) -> None:
        return None


class PostgresReserveStore:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._pool: Any = None

    async def get_reserve(self, idempotency_key: str) -> dict[str, Any] | None:
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            value = await connection.fetchval(
                "SELECT result FROM reserve_operations WHERE idempotency_key = $1",
                UUID(idempotency_key),
            )
        return json.loads(value) if value is not None else None

    async def save_reserve(self, idempotency_key: str, result: dict[str, Any]) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO reserve_operations (idempotency_key, result)
                VALUES ($1, $2::jsonb)
                ON CONFLICT (idempotency_key) DO NOTHING
                """,
                UUID(idempotency_key),
                json.dumps(result),
            )

    async def was_unreserved(self, order_id: str) -> bool:
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchval(
                "SELECT 1 FROM unreserve_operations WHERE order_id = $1",
                UUID(order_id),
            )
        return row is not None

    async def mark_unreserved(self, order_id: str) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO unreserve_operations (order_id)
                VALUES ($1)
                ON CONFLICT (order_id) DO NOTHING
                """,
                UUID(order_id),
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


def parse_reserve_request(payload: Any) -> ReserveRequest:
    if not isinstance(payload, Mapping):
        raise InvalidRequest("Request body must be a JSON object")
    return ReserveRequest(
        idempotency_key=_required_uuid(payload, "idempotency_key"),
        order_id=_required_uuid(payload, "order_id"),
        items=_parse_items(payload.get("items")),
    )


def parse_unreserve_request(payload: Any) -> UnreserveRequest:
    if not isinstance(payload, Mapping):
        raise InvalidRequest("Request body must be a JSON object")
    return UnreserveRequest(
        order_id=_required_uuid(payload, "order_id"),
        items=_parse_items(payload.get("items")),
    )


def to_reserve_response(response: ReserveResponse) -> dict[str, Any]:
    if response.reserved:
        # ReserveResponse per b2b.yaml.
        return {
            "order_id": response.order_id,
            "status": "RESERVED",
            "reserved_at": response.reserved_at,
        }
    # 409 body as Error per b2b.yaml; per-SKU detail lives under details.failed_items.
    return {
        "code": "INSUFFICIENT_STOCK",
        "message": "One or more SKUs could not be reserved",
        "details": {"failed_items": response.failed_items},
    }


def _parse_items(value: Any) -> tuple[ReserveItem, ...]:
    if not isinstance(value, list) or not value:
        raise InvalidRequest("items must be a non-empty array")
    items: list[ReserveItem] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise InvalidRequest(f"items[{index}] must be an object")
        sku_id = _required_uuid(raw, "sku_id")
        quantity = raw.get("quantity")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise InvalidRequest(f"items[{index}].quantity must be a positive integer")
        items.append(ReserveItem(sku_id=sku_id, quantity=quantity))
    return tuple(items)
