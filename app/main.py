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
from app.products import PostgresProductRepository, ProductRepository, ProductService
from app.routes import products


def create_app(product_repository: ProductRepository | None = None) -> FastAPI:
    repository = product_repository or PostgresProductRepository(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            await repository.aclose()

    app = FastAPI(
        title="NeoMarket B2B Seller Cabinet",
        version="0.1.0",
        description="B2B seller-cabinet service for NeoMarket.",
        lifespan=lifespan,
    )
    app.state.product_repository = repository
    app.state.product_service = ProductService(repository)

    app.add_exception_handler(ServiceError, service_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)

    app.include_router(products.router)
    return app


app = create_app()
