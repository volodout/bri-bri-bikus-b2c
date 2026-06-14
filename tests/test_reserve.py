from __future__ import annotations

from uuid import uuid4

from tests.conftest import b2c_service_headers, seed_sku


def reserve_body(items, idempotency_key=None, order_id=None):
    return {
        "idempotency_key": idempotency_key or str(uuid4()),
        "order_id": order_id or str(uuid4()),
        "items": [{"sku_id": sku_id, "quantity": qty} for sku_id, qty in items],
    }


def unreserve_body(items, order_id=None):
    return {
        "order_id": order_id or str(uuid4()),
        "items": [{"sku_id": sku_id, "quantity": qty} for sku_id, qty in items],
    }


# --- happy path -----------------------------------------------------------


async def test_reserve_all_skus_succeeds(client, sku_repository):
    pid = str(uuid4())
    sku1 = await seed_sku(sku_repository, product_id=pid, active_quantity=10)
    sku2 = await seed_sku(sku_repository, product_id=pid, active_quantity=5)

    async with client as ac:
        response = await ac.post(
            "/api/v1/inventory/reserve",
            json=reserve_body([(sku1.id, 2), (sku2.id, 1)]),
            headers=b2c_service_headers(),
        )

    assert response.status_code == 200
    body = response.json()
    # ReserveResponse per b2b.yaml.
    assert body["status"] == "RESERVED"
    assert body["reserved_at"]
    assert "order_id" in body

    s1 = await sku_repository.get_sku(sku1.id)
    s2 = await sku_repository.get_sku(sku2.id)
    assert (s1.active_quantity, s1.reserved_quantity) == (8, 2)
    assert (s2.active_quantity, s2.reserved_quantity) == (4, 1)


async def test_idempotent_reserve_returns_200_without_double_deduction(client, sku_repository):
    pid = str(uuid4())
    sku = await seed_sku(sku_repository, product_id=pid, active_quantity=10)
    key = str(uuid4())
    payload = reserve_body([(sku.id, 3)], idempotency_key=key)

    async with client as ac:
        first = await ac.post("/api/v1/inventory/reserve", json=payload, headers=b2c_service_headers())
        second = await ac.post("/api/v1/inventory/reserve", json=payload, headers=b2c_service_headers())

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    # Deducted exactly once.
    sku_after = await sku_repository.get_sku(sku.id)
    assert (sku_after.active_quantity, sku_after.reserved_quantity) == (7, 3)


async def test_sku_out_of_stock_event_emitted(client, sku_repository, b2c_gateway):
    pid = str(uuid4())
    sku = await seed_sku(sku_repository, product_id=pid, active_quantity=2)

    async with client as ac:
        response = await ac.post(
            "/api/v1/inventory/reserve",
            json=reserve_body([(sku.id, 2)]),
            headers=b2c_service_headers(),
        )

    assert response.status_code == 200
    sku_after = await sku_repository.get_sku(sku.id)
    assert sku_after.active_quantity == 0
    assert len(b2c_gateway.events) == 1
    assert b2c_gateway.events[0].event == "SKU_OUT_OF_STOCK"
    assert b2c_gateway.events[0].sku_id == sku.id


async def test_no_event_when_stock_remains(client, sku_repository, b2c_gateway):
    pid = str(uuid4())
    sku = await seed_sku(sku_repository, product_id=pid, active_quantity=5)

    async with client as ac:
        await ac.post("/api/v1/inventory/reserve", json=reserve_body([(sku.id, 2)]), headers=b2c_service_headers())

    assert b2c_gateway.events == []


async def test_unreserve_restores_quantities(client, sku_repository):
    pid = str(uuid4())
    sku = await seed_sku(sku_repository, product_id=pid, active_quantity=8, reserved_quantity=2)

    async with client as ac:
        response = await ac.post(
            "/api/v1/inventory/unreserve",
            json=unreserve_body([(sku.id, 2)]),
            headers=b2c_service_headers(),
        )

    assert response.status_code == 200
    body = response.json()
    # InventoryOrderResponse per b2b.yaml.
    assert body["status"] == "UNRESERVED"
    assert body["processed_at"]
    assert "order_id" in body
    sku_after = await sku_repository.get_sku(sku.id)
    assert (sku_after.active_quantity, sku_after.reserved_quantity) == (10, 0)


async def test_unreserve_idempotent_on_order_id(client, sku_repository):
    pid = str(uuid4())
    sku = await seed_sku(sku_repository, product_id=pid, active_quantity=8, reserved_quantity=2)
    payload = unreserve_body([(sku.id, 2)], order_id=str(uuid4()))

    async with client as ac:
        await ac.post("/api/v1/inventory/unreserve", json=payload, headers=b2c_service_headers())
        await ac.post("/api/v1/inventory/unreserve", json=payload, headers=b2c_service_headers())

    # Restored once, not twice.
    sku_after = await sku_repository.get_sku(sku.id)
    assert (sku_after.active_quantity, sku_after.reserved_quantity) == (10, 0)


# --- unhappy path ---------------------------------------------------------


async def test_partial_insufficient_stock_returns_409_all_rollback(client, sku_repository):
    pid = str(uuid4())
    sku1 = await seed_sku(sku_repository, product_id=pid, active_quantity=10)
    sku2 = await seed_sku(sku_repository, product_id=pid, active_quantity=3)

    async with client as ac:
        response = await ac.post(
            "/api/v1/inventory/reserve",
            json=reserve_body([(sku1.id, 2), (sku2.id, 5)]),
            headers=b2c_service_headers(),
        )

    assert response.status_code == 409
    body = response.json()
    assert body["reserved"] is False
    assert len(body["failed_items"]) == 1
    failed = body["failed_items"][0]
    assert failed == {
        "sku_id": sku2.id,
        "requested": 5,
        "available": 3,
        "reason": "INSUFFICIENT_STOCK",
    }
    # All-or-nothing: even the SKU that would have fit is untouched.
    s1 = await sku_repository.get_sku(sku1.id)
    s2 = await sku_repository.get_sku(sku2.id)
    assert (s1.active_quantity, s1.reserved_quantity) == (10, 0)
    assert (s2.active_quantity, s2.reserved_quantity) == (3, 0)


async def test_reserve_out_of_stock_reason(client, sku_repository):
    pid = str(uuid4())
    sku = await seed_sku(sku_repository, product_id=pid, active_quantity=0)

    async with client as ac:
        response = await ac.post(
            "/api/v1/inventory/reserve",
            json=reserve_body([(sku.id, 1)]),
            headers=b2c_service_headers(),
        )

    assert response.status_code == 409
    failed = response.json()["failed_items"][0]
    assert failed["reason"] == "OUT_OF_STOCK"
    assert failed["available"] == 0


async def test_reserve_requires_service_key(client, sku_repository):
    pid = str(uuid4())
    sku = await seed_sku(sku_repository, product_id=pid, active_quantity=5)
    payload = reserve_body([(sku.id, 1)])

    async with client as ac:
        missing = await ac.post("/api/v1/inventory/reserve", json=payload)
        wrong = await ac.post(
            "/api/v1/inventory/reserve", json=payload, headers={"X-Service-Key": "wrong-key"}
        )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    sku_after = await sku_repository.get_sku(sku.id)
    assert sku_after.active_quantity == 5  # untouched


async def test_reserve_invalid_quantity_returns_400(client, sku_repository):
    pid = str(uuid4())
    sku = await seed_sku(sku_repository, product_id=pid, active_quantity=5)

    async with client as ac:
        response = await ac.post(
            "/api/v1/inventory/reserve",
            json=reserve_body([(sku.id, 0)]),
            headers=b2c_service_headers(),
        )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_REQUEST"
