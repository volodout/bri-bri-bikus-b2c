# bri-bri-bikus-b2c - NeoMarket B2B Seller Cabinet

This repository contains the NeoMarket B2B seller-cabinet service. The
repository name says `b2c` by mistake; the service is B2B.

## Implemented Contracts

- US-B2B-01: product card creation via `POST /api/v1/products`.
- US-B2B-02: SKU creation via `POST /api/v1/skus`. Adding the first SKU to a
  `CREATED` product moves it to `ON_MODERATION` and emits a `CREATED` event to
  Moderation; adding a SKU to a `MODERATED`/`BLOCKED` product re-moderates it with
  an `EDITED` event (Founder ruling D-P8-01, 2026-05-27). The proposed OpenAPI for
  this endpoint lives in [`protocols/b2b/openapi.yaml`](protocols/b2b/openapi.yaml)
  (to be PR'd into `neomarket-protocols`, whose `/skus` body is still a stub).
- US-B2B-03: editing via `PUT /api/v1/products/{id}` and `PUT /api/v1/skus/{id}`.
  Ownership is enforced from the JWT (403 `NOT_OWNER` on someone else's resource);
  editing a `MODERATED`/`BLOCKED` product — or any of its SKUs — returns it to
  `ON_MODERATION` with an `EDITED` event. SKU reserves (`reserved_quantity`) are
  preserved on edit; `HARD_BLOCKED` products cannot be edited (403).
- US-B2B-05: product card view via `GET /api/v1/products/{id}`. Dual mode — seller
  cabinet (Bearer JWT, sees only own products: a foreign product is `404`, not
  `403`) or Moderation (`X-Service-Key`, sees any product). Returns the full
  seller payload including SKUs with `cost_price`/`reserved_quantity`, and for a
  `BLOCKED` product the `blocking_reason` and per-field `field_reports`.
- US-B2B-08: stock reservation via `POST /api/v1/reserve` and `POST /api/v1/unreserve`
  (B2C `X-Service-Key`). All-or-nothing: if any SKU is short, nothing is reserved and
  the response is `409` with `failed_items`. Idempotent — a repeated `idempotency_key`
  replays the cached result without double-deducting; `unreserve` is deduped by
  `order_id`. When a SKU's `active_quantity` reaches 0 a `SKU_OUT_OF_STOCK` event is
  sent to B2C. Invariant `active_quantity + reserved_quantity = on_hand` is preserved.

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

export DATABASE_URL=postgresql://neomarket:neomarket@localhost:5432/neomarket
export JWT_ALGORITHM=HS256
export JWT_SECRET=dev-jwt-secret-for-tests-32-bytes

# Cross-service URLs + keys (defaults exist; override per env)
export MODERATION_URL=http://moderation:8000
export B2B_TO_MOD_KEY=dev-b2b-to-mod-key
export MOD_TO_B2B_KEY=dev-mod-to-b2b-key
export B2C_TO_B2B_KEY=dev-b2c-to-b2b-key
export B2C_URL=http://b2c:8000
export B2B_TO_B2C_KEY=dev-b2b-to-b2c-key

python -m scripts.apply_migrations
uvicorn app.main:app --reload
```

## Tests

```bash
pytest -v
```

DoD tests for contract 01:

- `test_create_product_returns_201_with_created_status`
- `test_seller_id_taken_from_jwt`
- `test_missing_images_returns_400`
- `test_missing_category_returns_400`
- `test_invalid_category_id_returns_400`

DoD tests for contract 02 (`tests/test_skus.py`):

- `test_first_sku_transitions_product_to_on_moderation`
- `test_first_sku_emits_created_event_to_moderation`
- `test_second_sku_no_state_change`
- `test_add_sku_to_hard_blocked_returns_403`
- `test_missing_image_returns_400`

DoD tests for contract 03 (`tests/test_edit.py`):

- `test_edit_moderated_product_returns_to_on_moderation`
- `test_edit_blocked_product_returns_to_on_moderation`
- `test_reserves_preserved_after_sku_edit`
- `test_edit_hard_blocked_returns_403`
- `test_edit_others_product_returns_403`

DoD tests for contract 05 (`tests/test_view.py`):

- `test_get_moderated_product_returns_full_payload`
- `test_get_blocked_product_returns_blocking_reason_and_field_reports`
- `test_get_others_product_returns_404`
- `test_get_nonexistent_returns_404`

DoD tests for contract 08 (`tests/test_reserve.py`):

- `test_reserve_all_skus_succeeds`
- `test_partial_insufficient_stock_returns_409_all_rollback`
- `test_idempotent_reserve_returns_200_without_double_deduction`
- `test_sku_out_of_stock_event_emitted`
- `test_unreserve_restores_quantities`

## Structure

```text
app/
  main.py              FastAPI app factory and error handlers
  auth.py              Seller JWT extraction
  errors.py            Canonical {code, message} errors
  moderation.py        ProductEvent, ModerationGateway + Http/Recording impls
  products.py          Product domain, repositories, create/edit service
  skus.py              SKU domain, repositories (incl. atomic reserve/unreserve)
  inventory.py         Reserve/unreserve service, idempotency store, B2C gateway
  views.py             Read-side product-card view (GET) + serializer
  routes/products.py   Product HTTP routes (GET, POST, PUT)
  routes/skus.py       SKU HTTP routes (POST, PUT)
  routes/reserve.py    Reserve/unreserve HTTP routes (B2C service-to-service)
migrations/            Raw SQL migrations for asyncpg-based persistence
scripts/               Operational helpers
tests/                 Contract tests
protocols/b2b/         Proposed OpenAPI for neomarket-protocols (POST/PUT products + skus)
docs/adr/              ADR text for pull requests
```
