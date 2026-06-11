CREATE TABLE IF NOT EXISTS categories (
    id uuid PRIMARY KEY,
    name text NOT NULL,
    parent_id uuid REFERENCES categories(id) ON DELETE RESTRICT,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS products (
    id uuid PRIMARY KEY,
    seller_id uuid NOT NULL,
    category_id uuid NOT NULL REFERENCES categories(id) ON DELETE RESTRICT,
    title text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 255),
    slug text NOT NULL,
    description text NOT NULL CHECK (char_length(description) BETWEEN 1 AND 5000),
    status text NOT NULL CHECK (
        status IN ('CREATED', 'ON_MODERATION', 'MODERATED', 'BLOCKED', 'HARD_BLOCKED')
    ),
    deleted boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_products_seller_id ON products(seller_id);
CREATE INDEX IF NOT EXISTS idx_products_category_id ON products(category_id);
CREATE INDEX IF NOT EXISTS idx_products_status_deleted ON products(status, deleted);

CREATE TABLE IF NOT EXISTS product_images (
    id uuid PRIMARY KEY,
    product_id uuid NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    url text NOT NULL,
    ordering integer NOT NULL CHECK (ordering >= 0)
);

CREATE INDEX IF NOT EXISTS idx_product_images_product_id ON product_images(product_id);

CREATE TABLE IF NOT EXISTS product_characteristics (
    id uuid PRIMARY KEY,
    product_id uuid NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    name text NOT NULL,
    value text NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_product_characteristics_product_id
    ON product_characteristics(product_id);
CREATE INDEX IF NOT EXISTS idx_product_characteristics_name_value
    ON product_characteristics(name, value);
