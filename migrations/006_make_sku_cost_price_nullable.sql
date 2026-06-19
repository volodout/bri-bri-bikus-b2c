ALTER TABLE skus
    ALTER COLUMN cost_price DROP NOT NULL;

ALTER TABLE skus
    DROP CONSTRAINT IF EXISTS skus_cost_price_check;

ALTER TABLE skus
    ADD CONSTRAINT skus_cost_price_check
    CHECK (cost_price IS NULL OR cost_price > 0);
