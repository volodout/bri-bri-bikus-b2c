from __future__ import annotations

from typing import Any
from uuid import UUID

import jwt
from fastapi import Request

from app.config import settings
from app.errors import Forbidden, Unauthorized


def seller_id_from_jwt(request: Request) -> str:
    claims = _claims_from_request(request)

    if claims.get("role") != "seller":
        raise Forbidden()

    sub = claims.get("sub")
    if not isinstance(sub, str):
        raise Unauthorized("Invalid token")

    try:
        return str(UUID(sub))
    except ValueError:
        raise Unauthorized("Invalid token")


def viewer_from_request(request: Request) -> str | None:
    """Resolve the caller of GET /products/{id}.

    Returns the seller id for seller-cabinet mode, or None for a trusted
    inter-service call (Moderation) authenticated via ``X-Service-Key``. The
    caller treats ``None`` as "skip the ownership check". Raises Unauthorized
    when neither credential is valid.
    """
    service_key = request.headers.get("X-Service-Key")
    if service_key is not None:
        if service_key != settings.mod_to_b2b_key:
            raise Unauthorized("Invalid service key")
        return None
    return seller_id_from_jwt(request)


def _claims_from_request(request: Request) -> dict[str, Any]:
    authorization = request.headers.get("Authorization")
    if not authorization:
        raise Unauthorized()

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise Unauthorized()

    try:
        claims = jwt.decode(
            token,
            key=_jwt_key(),
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "role"]},
        )
    except jwt.ExpiredSignatureError:
        raise Unauthorized("Token expired")
    except jwt.InvalidTokenError:
        raise Unauthorized("Invalid token")

    if not isinstance(claims, dict):
        raise Unauthorized("Invalid token")
    return claims


def _jwt_key() -> str:
    if settings.jwt_algorithm == "HS256":
        return settings.jwt_secret
    if settings.jwt_algorithm == "RS256":
        if not settings.jwt_public_key:
            raise Unauthorized("Invalid token")
        return settings.jwt_public_key
    raise Unauthorized("Invalid token")
