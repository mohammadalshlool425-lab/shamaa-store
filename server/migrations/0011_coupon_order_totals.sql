-- Rebuild order tables so persisted totals enforce store-funded discounts.
-- Database.initialize disables foreign keys for this transaction, then runs
-- PRAGMA foreign_key_check before committing and restores enforcement.
-- Temporarily remove cross-table triggers while SQLite replaces referenced tables.
DROP TRIGGER IF EXISTS messages_sender_participant;
DROP TRIGGER IF EXISTS review_purchase_eligibility;
DROP TRIGGER IF EXISTS return_request_purchase_guard;
DROP TRIGGER IF EXISTS coupon_redemption_guard;

CREATE TABLE orders_rebuilt (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 number TEXT NOT NULL UNIQUE,
 buyer_id INTEGER NOT NULL REFERENCES users(id),
 idempotency_key TEXT NOT NULL,
 request_hash TEXT NOT NULL,
 recipient TEXT NOT NULL,
 phone TEXT NOT NULL,
 city TEXT NOT NULL,
 address TEXT NOT NULL,
 subtotal_fils INTEGER NOT NULL CHECK(subtotal_fils>=0),
 shipping_fils INTEGER NOT NULL CHECK(shipping_fils>=0),
 discount_fils INTEGER NOT NULL DEFAULT 0
   CHECK(discount_fils>=0 AND discount_fils<=subtotal_fils),
 total_fils INTEGER NOT NULL
   CHECK(total_fils=subtotal_fils+shipping_fils-discount_fils),
 payment_method TEXT NOT NULL DEFAULT 'cod' CHECK(payment_method='cod'),
 is_demo INTEGER NOT NULL DEFAULT 0,
 created_at INTEGER NOT NULL,
 UNIQUE(buyer_id,idempotency_key)
);
INSERT INTO orders_rebuilt(
 id,number,buyer_id,idempotency_key,request_hash,recipient,phone,city,address,
 subtotal_fils,shipping_fils,discount_fils,total_fils,payment_method,is_demo,created_at
)
SELECT
 id,number,buyer_id,idempotency_key,request_hash,recipient,phone,city,address,
 subtotal_fils,shipping_fils,discount_fils,total_fils,payment_method,is_demo,created_at
FROM orders;

CREATE TABLE seller_orders_rebuilt (
 id INTEGER PRIMARY KEY,
 order_id INTEGER NOT NULL REFERENCES orders(id),
 store_id INTEGER NOT NULL REFERENCES stores(id),
 status TEXT NOT NULL DEFAULT 'new'
   CHECK(status IN ('new','confirmed','preparing','shipped','delivered','cancelled')),
 subtotal_fils INTEGER NOT NULL,
 shipping_fils INTEGER NOT NULL,
 discount_fils INTEGER NOT NULL DEFAULT 0
   CHECK(discount_fils>=0 AND discount_fils<=subtotal_fils),
 commission_fils INTEGER NOT NULL,
 net_fils INTEGER NOT NULL
   CHECK(net_fils=subtotal_fils+shipping_fils-discount_fils-commission_fils),
 updated_at INTEGER NOT NULL,
 UNIQUE(order_id,store_id)
);
INSERT INTO seller_orders_rebuilt(
 id,order_id,store_id,status,subtotal_fils,shipping_fils,discount_fils,
 commission_fils,net_fils,updated_at
)
SELECT
 id,order_id,store_id,status,subtotal_fils,shipping_fils,discount_fils,
 commission_fils,net_fils,updated_at
FROM seller_orders;

DROP TABLE seller_orders;
DROP TABLE orders;
ALTER TABLE orders_rebuilt RENAME TO orders;
ALTER TABLE seller_orders_rebuilt RENAME TO seller_orders;

CREATE TRIGGER messages_sender_participant
BEFORE INSERT ON messages
WHEN NOT EXISTS (
 SELECT 1 FROM conversations c
 JOIN seller_orders so ON so.id=c.seller_order_id
 JOIN orders o ON o.id=so.order_id
 JOIN stores s ON s.id=so.store_id
 WHERE c.id=NEW.conversation_id
   AND (o.buyer_id=NEW.sender_id OR s.owner_id=NEW.sender_id)
)
BEGIN
 SELECT RAISE(ABORT,'message sender is not a conversation participant');
END;

CREATE TRIGGER review_purchase_eligibility
BEFORE INSERT ON reviews
WHEN NOT EXISTS (
 SELECT 1 FROM order_items oi
 JOIN seller_orders so ON so.id=oi.seller_order_id
 JOIN orders o ON o.id=so.order_id
 WHERE oi.id=NEW.order_item_id AND oi.product_id=NEW.product_id
   AND oi.seller_order_id=NEW.seller_order_id
   AND o.buyer_id=NEW.buyer_id AND so.status='delivered'
)
BEGIN SELECT RAISE(ABORT,'review requires buyer own delivered order item'); END;

CREATE TRIGGER return_request_purchase_guard
BEFORE INSERT ON return_requests
WHEN NOT EXISTS (
 SELECT 1
   FROM order_items oi
   JOIN seller_orders so ON so.id=oi.seller_order_id
   JOIN orders o ON o.id=so.order_id
   JOIN stores s ON s.id=so.store_id
   JOIN users buyer ON buyer.id=o.buyer_id
   JOIN users seller ON seller.id=s.owner_id
   JOIN order_events delivered ON delivered.seller_order_id=so.id AND delivered.to_status='delivered'
  WHERE oi.id=NEW.order_item_id
    AND oi.seller_order_id=NEW.seller_order_id
    AND o.buyer_id=NEW.buyer_id
    AND s.owner_id=NEW.seller_id
    AND so.status='delivered'
    AND buyer.active=1 AND buyer.email_verified_at IS NOT NULL
    AND seller.active=1
    AND delivered.created_at<=NEW.requested_at
    AND NEW.requested_at-delivered.created_at BETWEEN 0 AND 1209600
    AND NEW.quantity+COALESCE((
      SELECT SUM(existing.quantity) FROM return_requests existing
       WHERE existing.order_item_id=oi.id
         AND existing.status IN ('requested','approved','return_shipped','received','resolved')
    ),0)<=oi.quantity
)
BEGIN SELECT RAISE(ABORT,'return request is not eligible for this delivered purchased item'); END;

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
