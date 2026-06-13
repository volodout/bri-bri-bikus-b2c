CREATE TABLE IF NOT EXISTS skus (
    id uuid PRIMARY KEY,
    product_id uuid NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 255),
    price bigint NOT NULL CHECK (price > 0),
    cost_price bigint NOT NULL CHECK (cost_price > 0),
    discount bigint NOT NULL DEFAULT 0 CHECK (discount >= 0),
    image text NOT NULL,
    active_quantity integer NOT NULL DEFAULT 0 CHECK (active_quantity >= 0),
    reserved_quantity integer NOT NULL DEFAULT 0 CHECK (reserved_quantity >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_skus_product_id ON skus(product_id);

CREATE TABLE IF NOT EXISTS sku_characteristics (
    id uuid PRIMARY KEY,
    sku_id uuid NOT NULL REFERENCES skus(id) ON DELETE CASCADE,
    name text NOT NULL,
    value text NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sku_characteristics_sku_id
    ON sku_characteristics(sku_id);
