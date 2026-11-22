-- SHAM'A phase 2, batch 8: store-funded, single-store coupon redemption.
ALTER TABLE orders ADD COLUMN discount_fils INTEGER NOT NULL DEFAULT 0
 CHECK(discount_fils>=0 AND discount_fils<=total_fils);
ALTER TABLE seller_orders ADD COLUMN discount_fils INTEGER NOT NULL DEFAULT 0
 CHECK(discount_fils>=0 AND discount_fils<=subtotal_fils);

CREATE TABLE coupons (
 id INTEGER PRIMARY KEY,
 store_id INTEGER NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
 code TEXT NOT NULL UNIQUE COLLATE NOCASE
   CHECK(length(code) BETWEEN 3 AND 32),
 discount_type TEXT NOT NULL CHECK(discount_type IN ('percent','fixed')),
 discount_value INTEGER NOT NULL CHECK(
   (discount_type='percent' AND discount_value BETWEEN 1 AND 10000)
   OR (discount_type='fixed' AND discount_value BETWEEN 1 AND 100000000000)
 ),
 minimum_subtotal_fils INTEGER NOT NULL DEFAULT 0 CHECK(minimum_subtotal_fils>=0),
 maximum_discount_fils INTEGER CHECK(maximum_discount_fils IS NULL OR maximum_discount_fils>0),
 starts_at INTEGER,
 expires_at INTEGER,
 max_uses INTEGER CHECK(max_uses IS NULL OR max_uses>=1),
 max_uses_per_buyer INTEGER NOT NULL DEFAULT 1 CHECK(max_uses_per_buyer BETWEEN 1 AND 100),
 used_count INTEGER NOT NULL DEFAULT 0 CHECK(used_count>=0 AND (max_uses IS NULL OR used_count<=max_uses)),
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
 created_at INTEGER NOT NULL,
 updated_at INTEGER NOT NULL,
 CHECK(starts_at IS NULL OR starts_at>=0),
 CHECK(expires_at IS NULL OR expires_at>=0),
 CHECK(expires_at IS NULL OR starts_at IS NULL OR expires_at>starts_at)
);
CREATE INDEX coupons_store ON coupons(store_id,active,created_at);
CREATE TABLE coupon_redemptions (
 id INTEGER PRIMARY KEY,
 coupon_id INTEGER NOT NULL REFERENCES coupons(id) ON DELETE RESTRICT,
 order_id INTEGER NOT NULL UNIQUE REFERENCES orders(id) ON DELETE RESTRICT,
 seller_order_id INTEGER NOT NULL UNIQUE REFERENCES seller_orders(id) ON DELETE RESTRICT,
 store_id INTEGER NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
 buyer_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 code TEXT NOT NULL,
 discount_fils INTEGER NOT NULL CHECK(discount_fils>0),
 redeemed_at INTEGER NOT NULL,
 UNIQUE(coupon_id,buyer_id,order_id)
);
CREATE INDEX coupon_redemptions_buyer ON coupon_redemptions(coupon_id,buyer_id,id);
CREATE INDEX coupon_redemptions_store ON coupon_redemptions(store_id,id);

CREATE TRIGGER coupon_redemption_guard
BEFORE INSERT ON coupon_redemptions
WHEN NOT EXISTS (
 SELECT 1
   FROM coupons cp
   JOIN orders o ON o.id=NEW.order_id
   JOIN seller_orders so ON so.id=NEW.seller_order_id
  WHERE cp.id=NEW.coupon_id
    AND cp.store_id=NEW.store_id
    AND cp.code=NEW.code
    AND cp.used_count>0
    AND (cp.max_uses IS NULL OR cp.used_count<=cp.max_uses)
    AND o.buyer_id=NEW.buyer_id
    AND so.order_id=NEW.order_id
    AND so.store_id=NEW.store_id
    AND so.discount_fils=NEW.discount_fils
    AND o.discount_fils=NEW.discount_fils
    AND NEW.discount_fils<=so.subtotal_fils
    AND (
      SELECT COUNT(*) FROM coupon_redemptions existing
       WHERE existing.coupon_id=NEW.coupon_id AND existing.buyer_id=NEW.buyer_id
    )<cp.max_uses_per_buyer
)
BEGIN SELECT RAISE(ABORT,'coupon redemption does not match the order or usage limits'); END;

CREATE TRIGGER coupon_redemptions_immutable_update
BEFORE UPDATE ON coupon_redemptions
BEGIN SELECT RAISE(ABORT,'coupon redemptions are immutable'); END;
CREATE TRIGGER coupon_redemptions_immutable_delete
BEFORE DELETE ON coupon_redemptions
BEGIN SELECT RAISE(ABORT,'coupon redemptions are immutable'); END;
