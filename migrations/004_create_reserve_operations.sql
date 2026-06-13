-- Idempotency ledgers for reserve/unreserve (US-B2B-08).
-- A reserve is cached by its client-supplied idempotency_key; an unreserve is
-- deduped by the B2C order_id so a retried cancellation cannot double-restore.
CREATE TABLE IF NOT EXISTS reserve_operations (
    idempotency_key uuid PRIMARY KEY,
    result jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS unreserve_operations (
    order_id uuid PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now()
);
