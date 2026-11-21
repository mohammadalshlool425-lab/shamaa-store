-- Rebuild order tables so persisted totals enforce store-funded discounts.
-- Database.initialize disables foreign keys for this transaction, then runs
-- PRAGMA foreign_key_check before committing and restores enforcement.
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
