from __future__ import annotations

from json import JSONDecodeError

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.auth import seller_id_from_jwt
from app.errors import InvalidRequest
from app.invoices import InvoiceService, parse_invoice_create, to_invoice_response

router = APIRouter()


def get_invoice_service(request: Request) -> InvoiceService:
    return request.app.state.invoice_service


@router.post("/api/v1/invoices", status_code=201)
async def create_invoice(request: Request) -> JSONResponse:
    seller_id = seller_id_from_jwt(request)
    try:
        raw_payload = await request.json()
    except JSONDecodeError:
        raise InvalidRequest("Request body must be valid JSON")

    payload = parse_invoice_create(raw_payload)
    invoice = await get_invoice_service(request).create_invoice(seller_id, payload)
    return JSONResponse(status_code=201, content=to_invoice_response(invoice))
