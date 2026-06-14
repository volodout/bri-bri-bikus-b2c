from __future__ import annotations

from json import JSONDecodeError

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.auth import seller_id_from_jwt
from app.errors import InvalidRequest
from app.skus import SkuService, parse_sku_create, parse_sku_update, to_sku_response

router = APIRouter()


def get_sku_service(request: Request) -> SkuService:
    return request.app.state.sku_service


@router.post("/api/v1/skus", status_code=201)
async def create_sku(request: Request) -> JSONResponse:
    seller_id = seller_id_from_jwt(request)
    try:
        raw_payload = await request.json()
    except JSONDecodeError:
        raise InvalidRequest("Request body must be valid JSON")

    payload = parse_sku_create(raw_payload)
    sku = await get_sku_service(request).create_sku(seller_id, payload)
    return JSONResponse(status_code=201, content=to_sku_response(sku))


@router.patch("/api/v1/skus/{sku_id}", status_code=200)
async def update_sku(sku_id: str, request: Request) -> JSONResponse:
    seller_id = seller_id_from_jwt(request)
    try:
        raw_payload = await request.json()
    except JSONDecodeError:
        raise InvalidRequest("Request body must be valid JSON")

    payload = parse_sku_update(raw_payload)
    sku = await get_sku_service(request).update_sku(seller_id, sku_id, payload)
    return JSONResponse(status_code=200, content=to_sku_response(sku))
