# bri-bri-bikus-b2c - NeoMarket B2B Seller Cabinet

This repository contains the NeoMarket B2B seller-cabinet service. The
repository name says `b2c` by mistake; the service is B2B.

## Implemented Contracts

- US-B2B-01: product card creation via `POST /api/v1/products`.

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

export DATABASE_URL=postgresql://neomarket:neomarket@localhost:5432/neomarket
export JWT_ALGORITHM=HS256
export JWT_SECRET=dev-jwt-secret-for-tests-32-bytes

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

## Structure

```text
app/
  main.py              FastAPI app factory and error handlers
  auth.py              Seller JWT extraction
  errors.py            Canonical {code, message} errors
  products.py          Product domain, validation, repositories, serializer
  routes/products.py   HTTP routes
migrations/            Raw SQL migrations for asyncpg-based persistence
scripts/               Operational helpers
tests/                 Contract tests
docs/adr/              ADR text for pull requests
```
