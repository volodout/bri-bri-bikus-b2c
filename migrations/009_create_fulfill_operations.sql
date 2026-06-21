CREATE TABLE IF NOT EXISTS fulfill_operations (
    order_id uuid PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now()
);
