from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol
from uuid import UUID, uuid4

from app.errors import InvalidRequest, NotFound, ServiceUnavailable
from app.moderation import event_timestamp
from app.products import (
    BlockingReason,
    FieldReport,
    ProductRepository,
    ProductStatus,
    _required_uuid,
)
from app.skus import SkuRepository


@dataclass(frozen=True)
class ModerationEvent:
    idempotency_key: str
    product_id: str
    event_type: str  # MODERATED | BLOCKED
    occurred_at: str
    hard_block: bool = False
    blocking_reason_id: str | None = None
    moderator_comment: str | None = None
    field_reports: tuple[FieldReport, ...] = ()


@dataclass(frozen=True)
class ProductBlockedEvent:
    idempotency_key: str
    event: str
    product_id: str
    sku_ids: tuple[str, ...]
    date: str

    def as_payload(self) -> dict[str, Any]:
        return {
            "idempotency_key": self.idempotency_key,
            "event": self.event,
            "product_id": self.product_id,
            "sku_ids": list(self.sku_ids),
            "date": self.date,
        }


class B2CCatalogGateway(Protocol):
    async def publish_product_blocked(self, event: ProductBlockedEvent) -> None: ...


class ProcessedEventStore(Protocol):
    async def claim(self, idempotency_key: str) -> bool: ...

    async def aclose(self) -> None: ...


class ModerationApplyService:
    def __init__(
        self,
        product_repository: ProductRepository,
        sku_repository: SkuRepository,
        processed_store: ProcessedEventStore,
        b2c_gateway: B2CCatalogGateway,
    ) -> None:
        self._products = product_repository
        self._skus = sku_repository
        self._processed = processed_store
        self._b2c = b2c_gateway

    async def apply(self, event: ModerationEvent) -> None:
        product = await self._products.get_product(event.product_id)
        if product is None:
            # Not in the spec's enumerated codes, but a decision for an unknown
            # product is a genuine 404; the key is left unclaimed so a corrected
            # re-delivery can still be processed.
            raise NotFound("Product not found")

        # Claim the key before any side effect: a concurrent or re-delivered
        # duplicate loses the claim and exits without touching state.
        if not await self._processed.claim(event.idempotency_key):
            return

        if event.event_type == "MODERATED":
            await self._products.update_moderation_state(
                product.id, ProductStatus.MODERATED, None, ()
            )
            return

        # BLOCKED (soft or hard)
        new_status = (
            ProductStatus.HARD_BLOCKED if event.hard_block else ProductStatus.BLOCKED
        )
        blocking_reason = BlockingReason(
            id=event.blocking_reason_id or "",
            title="",  # the human title lives in Moderation's reason catalog, not the event
            comment=event.moderator_comment or "",
        )
        await self._products.update_moderation_state(
            product.id, new_status, blocking_reason, event.field_reports
        )
        skus = await self._skus.list_skus(product.id)
        await self._b2c.publish_product_blocked(
            ProductBlockedEvent(
                idempotency_key=str(uuid4()),
                event="PRODUCT_BLOCKED",
                product_id=product.id,
                sku_ids=tuple(sku.id for sku in skus),
                date=event_timestamp(),
            )
        )


class RecordingB2CCatalogGateway:
    """In-memory gateway used in tests; records every PRODUCT_BLOCKED cascade."""

    def __init__(self) -> None:
        self.events: list[ProductBlockedEvent] = []

    async def publish_product_blocked(self, event: ProductBlockedEvent) -> None:
        self.events.append(event)


class HttpB2CCatalogGateway:
    def __init__(
        self, base_url: str, service_key: str, *, transport: Any = None, timeout: float = 5.0
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._service_key = service_key
        self._transport = transport
        self._timeout = timeout

    async def publish_product_blocked(self, event: ProductBlockedEvent) -> None:
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
                raise ServiceUnavailable("B2C service is unavailable") from exc


class InMemoryProcessedEventStore:
    def __init__(self) -> None:
        self._seen: set[str] = set()

    async def claim(self, idempotency_key: str) -> bool:
        if idempotency_key in self._seen:
            return False
        self._seen.add(idempotency_key)
        return True

    async def aclose(self) -> None:
        return None


class PostgresProcessedEventStore:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._pool: Any = None

    async def claim(self, idempotency_key: str) -> bool:
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            claimed = await connection.fetchval(
                """
                INSERT INTO processed_events (idempotency_key)
                VALUES ($1)
                ON CONFLICT (idempotency_key) DO NOTHING
                RETURNING idempotency_key
                """,
                UUID(idempotency_key),
            )
        return claimed is not None

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def _get_pool(self):
        if self._pool is None:
            import asyncpg

            self._pool = await asyncpg.create_pool(dsn=self._database_url)
        return self._pool


def parse_moderation_event(payload: Any) -> ModerationEvent:
    if not isinstance(payload, Mapping):
        raise InvalidRequest("Request body must be a JSON object")

    idempotency_key = _required_uuid(payload, "idempotency_key")
    product_id = _required_uuid(payload, "product_id")

    event_type = payload.get("event_type")
    if event_type not in ("MODERATED", "BLOCKED"):
        raise InvalidRequest("event_type must be MODERATED or BLOCKED")

    occurred_at = payload.get("occurred_at")
    if not isinstance(occurred_at, str) or not occurred_at.strip():
        raise InvalidRequest("occurred_at is required")

    hard_block = payload.get("hard_block", False)
    if not isinstance(hard_block, bool):
        raise InvalidRequest("hard_block must be a boolean")

    moderator_comment = payload.get("moderator_comment")
    if moderator_comment is not None and not isinstance(moderator_comment, str):
        raise InvalidRequest("moderator_comment must be a string")

    blocking_reason_id: str | None = None
    field_reports: tuple[FieldReport, ...] = ()
    if event_type == "BLOCKED":
        blocking_reason_id = _required_uuid(payload, "blocking_reason_id")
        field_reports = _parse_field_reports(payload.get("field_reports"))

    return ModerationEvent(
        idempotency_key=idempotency_key,
        product_id=product_id,
        event_type=event_type,
        occurred_at=occurred_at,
        hard_block=hard_block,
        blocking_reason_id=blocking_reason_id,
        moderator_comment=moderator_comment,
        field_reports=field_reports,
    )


def _parse_field_reports(value: Any) -> tuple[FieldReport, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise InvalidRequest("field_reports must be an array")
    reports: list[FieldReport] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise InvalidRequest(f"field_reports[{index}] must be an object")
        field_name = raw.get("field_name")
        if not isinstance(field_name, str) or not field_name.strip():
            raise InvalidRequest(f"field_reports[{index}].field_name is required")
        comment = raw.get("comment")
        if not isinstance(comment, str) or not comment.strip():
            raise InvalidRequest(f"field_reports[{index}].comment is required")
        sku_id = raw.get("sku_id")
        if sku_id is not None:
            sku_id = _required_uuid(raw, "sku_id")
        reports.append(FieldReport(field_name=field_name.strip(), sku_id=sku_id, comment=comment))
    return tuple(reports)
