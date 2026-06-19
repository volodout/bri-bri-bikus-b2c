from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import uuid4

from app.errors import ServiceUnavailable
from app.moderation import event_timestamp


@dataclass(frozen=True)
class ProductDeletedEvent:
    product_id: str
    sku_ids: tuple[str, ...]
    idempotency_key: str = field(default_factory=lambda: str(uuid4()))
    event: str = "PRODUCT_DELETED"
    date: str = field(default_factory=event_timestamp)

    def as_payload(self) -> dict[str, Any]:
        return {
            "idempotency_key": self.idempotency_key,
            "event": self.event,
            "product_id": self.product_id,
            "sku_ids": list(self.sku_ids),
            "date": self.date,
        }


class ProductDeletionGateway(Protocol):
    async def publish_product_deleted(self, event: ProductDeletedEvent) -> None: ...


class RecordingProductDeletionGateway:
    """In-memory B2C gateway used in tests; records PRODUCT_DELETED cascades."""

    def __init__(self) -> None:
        self.events: list[ProductDeletedEvent] = []

    async def publish_product_deleted(self, event: ProductDeletedEvent) -> None:
        self.events.append(event)


class HttpProductDeletionGateway:
    def __init__(
        self, base_url: str, service_key: str, *, transport: Any = None, timeout: float = 5.0
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._service_key = service_key
        self._transport = transport
        self._timeout = timeout

    async def publish_product_deleted(self, event: ProductDeletedEvent) -> None:
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
