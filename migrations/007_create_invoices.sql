CREATE TABLE IF NOT EXISTS invoices (
    id uuid PRIMARY KEY,
    seller_id uuid NOT NULL,
    status text NOT NULL CHECK (
        status IN ('PENDING', 'ACCEPTED', 'PARTIALLY_ACCEPTED', 'REJECTED')
    ),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_invoices_seller_id ON invoices(seller_id);
CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices(status);

CREATE TABLE IF NOT EXISTS invoice_items (
    id uuid PRIMARY KEY,
    invoice_id uuid NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    sku_id uuid NOT NULL REFERENCES skus(id) ON DELETE RESTRICT,
    sku_name text NOT NULL,
    quantity integer NOT NULL CHECK (quantity > 0),
    accepted_quantity integer CHECK (
        accepted_quantity IS NULL
        OR (accepted_quantity >= 0 AND accepted_quantity <= quantity)
    )
);

CREATE INDEX IF NOT EXISTS idx_invoice_items_invoice_id ON invoice_items(invoice_id);
CREATE INDEX IF NOT EXISTS idx_invoice_items_sku_id ON invoice_items(sku_id);
