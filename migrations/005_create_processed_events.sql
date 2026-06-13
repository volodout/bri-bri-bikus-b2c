-- Idempotency ledger for inbound Moderation decisions (US-B2B-09). The
-- idempotency_key is claimed atomically (INSERT ... ON CONFLICT DO NOTHING)
-- before any side effect, so a re-delivered event produces no double effects.
CREATE TABLE IF NOT EXISTS processed_events (
    idempotency_key uuid PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now()
);
