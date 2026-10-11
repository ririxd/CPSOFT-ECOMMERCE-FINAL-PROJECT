ALTER TABLE sellers ADD COLUMN IF NOT EXISTS payment_details TEXT NOT NULL DEFAULT '';
ALTER TABLE checkouts ADD COLUMN IF NOT EXISTS fulfillment_status TEXT NOT NULL DEFAULT 'packed';

CREATE TABLE IF NOT EXISTS cart_items (
    buyer_id BIGINT NOT NULL REFERENCES registered_accounts(id) ON DELETE CASCADE,
    item_id BIGINT NOT NULL REFERENCES listed_items(id) ON DELETE CASCADE,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    created_at BIGINT NOT NULL DEFAULT ((extract(epoch FROM clock_timestamp()) * 1000)::bigint),
    PRIMARY KEY (buyer_id, item_id)
);
CREATE INDEX IF NOT EXISTS cart_items_buyer ON cart_items (buyer_id, created_at DESC);
