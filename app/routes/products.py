from __future__ import annotations

from json import JSONDecodeError

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.auth import seller_id_from_jwt
from app.errors import InvalidRequest
from app.products import ProductService, parse_product_create, to_product_response

router = APIRouter()


def get_product_service(request: Request) -> ProductService:
    return request.app.state.product_service


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
