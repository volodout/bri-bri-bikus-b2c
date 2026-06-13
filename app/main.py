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
from app.moderation import HttpModerationGateway, ModerationGateway
from app.products import PostgresProductRepository, ProductRepository, ProductService
from app.routes import products, skus
from app.skus import PostgresSkuRepository, SkuRepository, SkuService
from app.views import ProductViewService


def create_app(
    product_repository: ProductRepository | None = None,
    sku_repository: SkuRepository | None = None,
    moderation_gateway: ModerationGateway | None = None,
) -> FastAPI:
    repository = product_repository or PostgresProductRepository(settings.database_url)
    sku_repo = sku_repository or PostgresSkuRepository(settings.database_url)
    gateway = moderation_gateway or HttpModerationGateway(
        settings.moderation_url, settings.b2b_to_mod_key
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            await repository.aclose()
            await sku_repo.aclose()

    app = FastAPI(
        title="NeoMarket B2B Seller Cabinet",
        version="0.1.0",
        description="B2B seller-cabinet service for NeoMarket.",
        lifespan=lifespan,
    )
    app.state.product_repository = repository
    app.state.sku_repository = sku_repo
    app.state.product_service = ProductService(repository, gateway)
    app.state.sku_service = SkuService(repository, sku_repo, gateway)
    app.state.product_view_service = ProductViewService(repository, sku_repo)

    app.add_exception_handler(ServiceError, service_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)

    app.include_router(products.router)
    app.include_router(skus.router)
    return app


app = create_app()
