from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from app.errors import ServiceUnavailable

# Internal event kind -> Moderation's IncomingB2BEvent.event_type wire value.
_WIRE_EVENT_TYPE = {"CREATED": "PRODUCT_CREATED", "EDITED": "PRODUCT_EDITED"}


@dataclass(frozen=True)
class ProductEvent:
    idempotency_key: str
    product_id: str
    seller_id: str
    event: str  # internal kind: "CREATED" | "EDITED"
    date: str  # UTC ISO-8601 with milliseconds + Z
    json_after: dict[str, Any] = field(default_factory=dict)
    json_before: dict[str, Any] | None = None
    category_id: str | None = None

    def as_payload(self) -> dict[str, Any]:
        # Shape required by Moderation's IncomingB2BEvent
        # (POST /api/v1/b2b/events).
        payload: dict[str, Any] = {
            "product_id": self.product_id,
            "seller_id": self.seller_id,
        }
        if self.category_id is not None:
            payload["category_id"] = self.category_id
        if self.event == "EDITED":
            payload["json_before"] = self.json_before if self.json_before is not None else {}
        payload["json_after"] = self.json_after
        return {
            "event_type": _WIRE_EVENT_TYPE[self.event],
            "idempotency_key": self.idempotency_key,
            "occurred_at": self.date,
            "payload": payload,
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
                    f"{self._base_url}/api/v1/b2b/events",
                    headers={"X-Service-Key": self._service_key},
                    json=event.as_payload(),
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise ServiceUnavailable("Moderation service is unavailable") from exc


def event_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
