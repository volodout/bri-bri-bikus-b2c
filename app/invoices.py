from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Protocol
from uuid import UUID, uuid4

from app.errors import InvalidRequest, NotFound, NotOwner
from app.products import ProductRepository, ProductStatus, _required_uuid, _serialize_datetime, _utcnow
from app.skus import SkuRepository


@dataclass(frozen=True)
class InvoiceItemCreate:
    sku_id: str
    quantity: int


@dataclass(frozen=True)
class InvoiceCreate:
    items: tuple[InvoiceItemCreate, ...]


@dataclass(frozen=True)
class InvoiceItem:
    sku_id: str
    sku_name: str
    quantity: int
    accepted_quantity: int | None = None


@dataclass(frozen=True)
class Invoice:
    id: str
    seller_id: str
    status: str
    items: tuple[InvoiceItem, ...]
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)


class InvoiceRepository(Protocol):
    async def create_invoice(self, invoice: Invoice) -> Invoice: ...

    async def aclose(self) -> None: ...


class InvoiceService:
    def __init__(
        self,
        product_repository: ProductRepository,
        sku_repository: SkuRepository,
        invoice_repository: InvoiceRepository,
    ) -> None:
        self._products = product_repository
        self._skus = sku_repository
        self._invoices = invoice_repository

    async def create_invoice(self, seller_id: str, payload: InvoiceCreate) -> Invoice:
        items: list[InvoiceItem] = []
        for requested in payload.items:
            sku = await self._skus.get_sku(requested.sku_id)
            if sku is None:
                raise NotFound("SKU not found")

            product = await self._products.get_product(sku.product_id)
            if product is None:
                raise NotFound("SKU not found")
            if product.seller_id != seller_id:
                raise NotOwner("One or more SKUs do not belong to the authenticated seller")
            if product.status != ProductStatus.MODERATED or product.deleted:
                raise InvalidRequest("Invoice can only be created for MODERATED products")

            items.append(
                InvoiceItem(
                    sku_id=sku.id,
                    sku_name=sku.name,
                    quantity=requested.quantity,
                    accepted_quantity=None,
                )
            )

        invoice = Invoice(
            id=str(uuid4()),
            seller_id=seller_id,
            status="PENDING",
            items=tuple(items),
        )
        return await self._invoices.create_invoice(invoice)


class InMemoryInvoiceRepository:
    def __init__(self) -> None:
        self._invoices: dict[str, Invoice] = {}

    async def create_invoice(self, invoice: Invoice) -> Invoice:
        self._invoices[invoice.id] = invoice
        return invoice

    async def get_invoice(self, invoice_id: str) -> Invoice | None:
        return self._invoices.get(invoice_id)

    async def list_invoices(self) -> tuple[Invoice, ...]:
        return tuple(self._invoices.values())

    async def aclose(self) -> None:
        return None


class PostgresInvoiceRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._pool: Any = None

    async def create_invoice(self, invoice: Invoice) -> Invoice:
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    INSERT INTO invoices (id, seller_id, status, created_at, updated_at)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    UUID(invoice.id),
                    UUID(invoice.seller_id),
                    invoice.status,
                    invoice.created_at,
                    invoice.updated_at,
                )
                for item in invoice.items:
                    await connection.execute(
                        """
                        INSERT INTO invoice_items (
                            id, invoice_id, sku_id, sku_name, quantity, accepted_quantity
                        )
                        VALUES ($1, $2, $3, $4, $5, $6)
                        """,
                        uuid4(),
                        UUID(invoice.id),
                        UUID(item.sku_id),
                        item.sku_name,
                        item.quantity,
                        item.accepted_quantity,
                    )
        return invoice

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def _get_pool(self):
        if self._pool is None:
            import asyncpg

            self._pool = await asyncpg.create_pool(dsn=self._database_url)
        return self._pool


def parse_invoice_create(payload: Any) -> InvoiceCreate:
    if not isinstance(payload, Mapping):
        raise InvalidRequest("Request body must be a JSON object")

    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise InvalidRequest("At least one item is required")

    items: list[InvoiceItemCreate] = []
    for raw in raw_items:
        if not isinstance(raw, Mapping):
            raise InvalidRequest("items must be an array of objects")
        sku_id = _required_uuid(raw, "sku_id")
        quantity = raw.get("quantity")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise InvalidRequest("quantity must be > 0")
        items.append(InvoiceItemCreate(sku_id=sku_id, quantity=quantity))

    return InvoiceCreate(items=tuple(items))


def to_invoice_response(invoice: Invoice) -> dict[str, Any]:
    return {
        "id": invoice.id,
        "status": invoice.status,
        "created_at": _serialize_datetime(invoice.created_at),
        "items": [
            {
                "sku_id": item.sku_id,
                "sku_name": item.sku_name,
                "quantity": item.quantity,
                "accepted_quantity": item.accepted_quantity,
            }
            for item in invoice.items
        ],
    }
