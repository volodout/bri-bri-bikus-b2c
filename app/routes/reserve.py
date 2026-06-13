from __future__ import annotations

from json import JSONDecodeError

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.auth import require_service_key
from app.config import settings
from app.errors import InvalidRequest
from app.inventory import (
    InventoryService,
    parse_reserve_request,
    parse_unreserve_request,
    to_reserve_response,
)

router = APIRouter()


def get_inventory_service(request: Request) -> InventoryService:
    return request.app.state.inventory_service


async def _json_body(request: Request):
    try:
        return await request.json()
    except JSONDecodeError:
        raise InvalidRequest("Request body must be valid JSON")


@router.post("/api/v1/reserve")
async def reserve(request: Request) -> JSONResponse:
    require_service_key(request, settings.b2c_to_b2b_key)
    payload = parse_reserve_request(await _json_body(request))
    result = await get_inventory_service(request).reserve(payload)
    status_code = 200 if result.reserved else 409
    return JSONResponse(status_code=status_code, content=to_reserve_response(result))


@router.post("/api/v1/unreserve", status_code=200)
async def unreserve(request: Request) -> JSONResponse:
    require_service_key(request, settings.b2c_to_b2b_key)
    payload = parse_unreserve_request(await _json_body(request))
    await get_inventory_service(request).unreserve(payload)
    return JSONResponse(status_code=200, content={"ok": True})
