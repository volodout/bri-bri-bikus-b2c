-- Moderation feedback shown on a BLOCKED product card (US-B2B-05). Populated by
-- Moderation via the receive-events path; B2B stores and serves it back.
CREATE TABLE IF NOT EXISTS product_blocking_reasons (
    product_id uuid PRIMARY KEY REFERENCES products(id) ON DELETE CASCADE,
    reason_id uuid NOT NULL,
    title text NOT NULL,
    comment text NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS product_field_reports (
    id uuid PRIMARY KEY,
    product_id uuid NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    field_name text NOT NULL,
    sku_id uuid,
    comment text NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_product_field_reports_product_id
    ON product_field_reports(product_id);
