from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.errors import (
    ServiceError,
    http_exception_handler,
    service_error_handler,
    validation_exception_handler,
)
from app.b2c_events import (
    HttpProductDeletionGateway,
    ProductDeletionGateway,
)
from app.inventory import (
    B2CGateway,
    HttpB2CGateway,
    InventoryService,
    PostgresReserveStore,
    ReserveStore,
)
from app.catalog import CatalogService
from app.moderation import HttpModerationGateway, ModerationGateway
from app.moderation_inbound import (
    B2CCatalogGateway,
    HttpB2CCatalogGateway,
    ModerationApplyService,
    PostgresProcessedEventStore,
    ProcessedEventStore,
)
from app.products import PostgresProductRepository, ProductRepository, ProductService
from app.routes import catalog, moderation, products, reserve, skus
from app.skus import PostgresSkuRepository, SkuRepository, SkuService
from app.views import ProductViewService


def create_app(
    product_repository: ProductRepository | None = None,
    sku_repository: SkuRepository | None = None,
    moderation_gateway: ModerationGateway | None = None,
    reserve_store: ReserveStore | None = None,
    b2c_gateway: B2CGateway | None = None,
    processed_event_store: ProcessedEventStore | None = None,
    b2c_catalog_gateway: B2CCatalogGateway | None = None,
    product_deletion_gateway: ProductDeletionGateway | None = None,
) -> FastAPI:
    repository = product_repository or PostgresProductRepository(settings.database_url)
    sku_repo = sku_repository or PostgresSkuRepository(settings.database_url)
    gateway = moderation_gateway or HttpModerationGateway(
        settings.moderation_url, settings.b2b_to_mod_key
    )
    store = reserve_store or PostgresReserveStore(settings.database_url)
    b2c = b2c_gateway or HttpB2CGateway(settings.b2c_url, settings.b2b_to_b2c_key)
    processed_store = processed_event_store or PostgresProcessedEventStore(settings.database_url)
    b2c_catalog = b2c_catalog_gateway or HttpB2CCatalogGateway(
        settings.b2c_url, settings.b2b_to_b2c_key
    )
    product_deletion = product_deletion_gateway or HttpProductDeletionGateway(
        settings.b2c_url, settings.b2b_to_b2c_key
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            await repository.aclose()
            await sku_repo.aclose()
            await store.aclose()
            await processed_store.aclose()

    app = FastAPI(
        title="NeoMarket B2B Seller Cabinet",
        version="0.1.0",
        description="B2B seller-cabinet service for NeoMarket.",
        lifespan=lifespan,
    )
    app.state.product_repository = repository
    app.state.sku_repository = sku_repo
    app.state.product_service = ProductService(repository, gateway, sku_repo, product_deletion)
    app.state.sku_service = SkuService(repository, sku_repo, gateway)
    app.state.product_view_service = ProductViewService(repository, sku_repo)
    app.state.reserve_store = store
    app.state.inventory_service = InventoryService(sku_repo, store, b2c)
    app.state.moderation_apply_service = ModerationApplyService(
        repository, processed_store, b2c_catalog
    )
    app.state.catalog_service = CatalogService(repository, sku_repo)

    app.add_exception_handler(ServiceError, service_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)

    app.include_router(products.router)
    app.include_router(skus.router)
    app.include_router(reserve.router)
    app.include_router(moderation.router)
    app.include_router(catalog.router)
    return app


app = create_app()
