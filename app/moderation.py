from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from app.errors import ServiceUnavailable


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


class RecordingModerationGateway:
    """In-memory gateway used in tests; records every published event."""

    def __init__(self) -> None:
        self.events: list[ProductEvent] = []

    async def publish_product_event(self, event: ProductEvent) -> None:
        self.events.append(event)


class HttpModerationGateway:
    """Synchronous delivery: POST the event to Moderation in the request flow.

    See the event-delivery ADR in the US-B2B-02 PR for the trade-off.
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


def event_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
