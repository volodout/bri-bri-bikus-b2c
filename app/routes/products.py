from __future__ import annotations

from json import JSONDecodeError

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from app.auth import seller_id_from_jwt, viewer_from_request
from app.errors import InvalidRequest
from app.products import (
    ProductService,
    parse_product_create,
    parse_product_update,
    to_product_response,
)
from app.views import ProductViewService, to_product_view

router = APIRouter()


def get_product_service(request: Request) -> ProductService:
    return request.app.state.product_service


def get_product_view_service(request: Request) -> ProductViewService:
    return request.app.state.product_view_service


@router.get("/api/v1/products", status_code=200)
async def list_products(
    request: Request,
    limit: int = 20,
    offset: int = 0,
    status: str | None = None,
    search: str | None = None,
) -> JSONResponse:
    seller_id = seller_id_from_jwt(request)
    result = await get_product_service(request).list_seller_products(
        seller_id,
        limit=limit,
        offset=offset,
        status=status,
        search=search,
    )
    return JSONResponse(status_code=200, content=result)


@router.get("/api/v1/products/{product_id}", status_code=200)
async def get_product(product_id: str, request: Request) -> JSONResponse:
    # Dual mode: seller (Bearer JWT, ownership enforced) or Moderation
    # (X-Service-Key, sees any product). viewer is None in service mode.
    viewer = viewer_from_request(request)
    product, skus = await get_product_view_service(request).get_product_view(
        product_id, seller_id=viewer
    )
    return JSONResponse(status_code=200, content=to_product_view(product, skus))


@router.post("/api/v1/products", status_code=201)
async def create_product(request: Request) -> JSONResponse:
    seller_id = seller_id_from_jwt(request)
    try:
        raw_payload = await request.json()
    except JSONDecodeError:
        raise InvalidRequest("Request body must be valid JSON")

    payload = parse_product_create(raw_payload)
    product = await get_product_service(request).create_product(seller_id, payload)
    return JSONResponse(status_code=201, content=to_product_response(product))


@router.patch("/api/v1/products/{product_id}", status_code=200)
async def update_product(product_id: str, request: Request) -> JSONResponse:
    seller_id = seller_id_from_jwt(request)
    try:
        raw_payload = await request.json()
    except JSONDecodeError:
        raise InvalidRequest("Request body must be valid JSON")

    payload = parse_product_update(raw_payload)
    product = await get_product_service(request).update_product(seller_id, product_id, payload)
    return JSONResponse(status_code=200, content=to_product_response(product))


@router.delete("/api/v1/products/{product_id}", status_code=204)
async def delete_product(product_id: str, request: Request) -> Response:
    seller_id = seller_id_from_jwt(request)
    await get_product_service(request).delete_product(seller_id, product_id)
    return Response(status_code=204)
