from __future__ import annotations

from collections.abc import Mapping
from json import JSONDecodeError
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.auth import require_service_key
from app.catalog import CatalogService
from app.config import settings
from app.errors import InvalidRequest
from app.products import _is_uuid

router = APIRouter()


def get_catalog_service(request: Request) -> CatalogService:
    return request.app.state.catalog_service


@router.get("/api/v1/public/products", status_code=200)
async def list_public_products(
    request: Request,
    limit: int = 20,
    offset: int = 0,
    category_id: str | None = None,
    search: str | None = None,
    sort: str | None = None,
    min_price: int | None = None,
    max_price: int | None = None,
    seller_id: str | None = None,
) -> JSONResponse:
    require_service_key(request, settings.b2c_to_b2b_key)
    result = await get_catalog_service(request).list_catalog(
        limit=limit,
        offset=offset,
        category_id=category_id,
        search=search,
        sort=sort,
        min_price=min_price,
        max_price=max_price,
        seller_id=seller_id,
    )
    return JSONResponse(status_code=200, content=result)


@router.post("/api/v1/public/products/batch", status_code=200)
async def batch_public_products(request: Request) -> JSONResponse:
    require_service_key(request, settings.b2c_to_b2b_key)
    try:
        raw_payload = await request.json()
    except JSONDecodeError:
        raise InvalidRequest("Request body must be valid JSON")

    product_ids = _parse_product_ids(raw_payload)
    result = await get_catalog_service(request).batch(product_ids)
    return JSONResponse(status_code=200, content=result)


def _parse_product_ids(payload: Any) -> list[str]:
    if not isinstance(payload, Mapping):
        raise InvalidRequest("Request body must be a JSON object")
    ids = payload.get("product_ids")
    if not isinstance(ids, list) or not ids:
        raise InvalidRequest("product_ids must be a non-empty array")
    if len(ids) > 100:
        raise InvalidRequest("product_ids must contain at most 100 items")
    result: list[str] = []
    for index, value in enumerate(ids):
        if not isinstance(value, str) or not _is_uuid(value):
            raise InvalidRequest(f"product_ids[{index}] must be a valid UUID")
        result.append(value)
    return result
