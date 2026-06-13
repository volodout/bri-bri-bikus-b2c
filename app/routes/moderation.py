from __future__ import annotations

from json import JSONDecodeError

from fastapi import APIRouter, Request
from starlette.responses import Response

from app.auth import require_service_key
from app.config import settings
from app.errors import InvalidRequest
from app.moderation_inbound import ModerationApplyService, parse_moderation_event

router = APIRouter()


def get_apply_service(request: Request) -> ModerationApplyService:
    return request.app.state.moderation_apply_service


@router.post("/api/v1/moderation/events", status_code=204)
async def receive_moderation_event(request: Request) -> Response:
    require_service_key(request, settings.mod_to_b2b_key)
    try:
        raw_payload = await request.json()
    except JSONDecodeError:
        raise InvalidRequest("Request body must be valid JSON")

    event = parse_moderation_event(raw_payload)
    await get_apply_service(request).apply(event)
    return Response(status_code=204)
