CREATE TABLE users (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL COLLATE NOCASE UNIQUE,
 password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'buyer' CHECK(role IN ('buyer','seller','admin')),
 locale TEXT NOT NULL DEFAULT 'ar' CHECK(locale IN ('ar','en')), active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
 email_verified_at INTEGER, mfa_secret TEXT, mfa_last_step INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL
);
CREATE TABLE sessions (
 token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), csrf_token TEXT NOT NULL,
 created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, ip_hash TEXT NOT NULL, user_agent TEXT NOT NULL,
 mfa_at INTEGER
);
CREATE INDEX sessions_user ON sessions(user_id);
CREATE TABLE auth_tokens (
 token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
 purpose TEXT NOT NULL CHECK(purpose IN ('verify','reset')), expires_at INTEGER NOT NULL, used_at INTEGER
);
CREATE TABLE rate_limits (bucket TEXT PRIMARY KEY, window_start INTEGER NOT NULL, count INTEGER NOT NULL);
CREATE TABLE categories (
 id TEXT PRIMARY KEY, name_ar TEXT NOT NULL, name_en TEXT NOT NULL, icon TEXT NOT NULL,
 parent_id TEXT REFERENCES categories(id), commission_bps INTEGER CHECK(commission_bps BETWEEN 0 AND 5000)
);
CREATE TABLE stores (
 id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL UNIQUE REFERENCES users(id), slug TEXT NOT NULL UNIQUE,
 name_ar TEXT NOT NULL, name_en TEXT NOT NULL, description_ar TEXT NOT NULL DEFAULT '', description_en TEXT NOT NULL DEFAULT '',
 city TEXT NOT NULL, accent TEXT NOT NULL DEFAULT 'green' CHECK(accent IN ('green','sand','blue')),
 shipping_fils INTEGER NOT NULL DEFAULT 3000 CHECK(shipping_fils BETWEEN 0 AND 100000),
 delivery_days INTEGER NOT NULL DEFAULT 3 CHECK(delivery_days BETWEEN 1 AND 30),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','active','rejected','suspended')),
 review_note TEXT NOT NULL DEFAULT '', commission_bps INTEGER CHECK(commission_bps BETWEEN 0 AND 5000),
 is_demo INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL
);
CREATE TABLE media (
 id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES users(id), digest TEXT NOT NULL,
 storage_key TEXT NOT NULL UNIQUE, bytes INTEGER NOT NULL, created_at INTEGER NOT NULL, UNIQUE(owner_id,digest)
);
CREATE TABLE products (
 id INTEGER PRIMARY KEY, store_id INTEGER NOT NULL REFERENCES stores(id), category_id TEXT NOT NULL REFERENCES categories(id),
 title_ar TEXT NOT NULL, title_en TEXT NOT NULL, description_ar TEXT NOT NULL, description_en TEXT NOT NULL,
 price_fils INTEGER NOT NULL CHECK(price_fils BETWEEN 1 AND 1000000000),
 compare_at_fils INTEGER CHECK(compare_at_fils IS NULL OR compare_at_fils>price_fils),
 stock INTEGER NOT NULL CHECK(stock BETWEEN 0 AND 1000000), sku TEXT NOT NULL,
 media_id INTEGER REFERENCES media(id), demo_image TEXT,
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('draft','pending','active','paused','rejected')),
 review_note TEXT NOT NULL DEFAULT '', featured INTEGER NOT NULL DEFAULT 0,
 version INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, UNIQUE(store_id,sku)
);
CREATE INDEX products_catalog ON products(status,category_id,price_fils);
CREATE INDEX products_store ON products(store_id,status);
CREATE TABLE carts (
 user_id INTEGER NOT NULL REFERENCES users(id), product_id INTEGER NOT NULL REFERENCES products(id),
 quantity INTEGER NOT NULL CHECK(quantity BETWEEN 1 AND 99), PRIMARY KEY(user_id,product_id)
);
CREATE TABLE orders (
 id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT NOT NULL UNIQUE, buyer_id INTEGER NOT NULL REFERENCES users(id),
 idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL,
 recipient TEXT NOT NULL, phone TEXT NOT NULL, city TEXT NOT NULL, address TEXT NOT NULL,
 subtotal_fils INTEGER NOT NULL CHECK(subtotal_fils>=0), shipping_fils INTEGER NOT NULL CHECK(shipping_fils>=0),
 total_fils INTEGER NOT NULL CHECK(total_fils=subtotal_fils+shipping_fils),
 payment_method TEXT NOT NULL DEFAULT 'cod' CHECK(payment_method='cod'), is_demo INTEGER NOT NULL DEFAULT 0,
 created_at INTEGER NOT NULL, UNIQUE(buyer_id,idempotency_key)
);
CREATE TABLE seller_orders (
 id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id), store_id INTEGER NOT NULL REFERENCES stores(id),
 status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new','confirmed','preparing','shipped','delivered','cancelled')),
 subtotal_fils INTEGER NOT NULL, shipping_fils INTEGER NOT NULL, commission_fils INTEGER NOT NULL,
 net_fils INTEGER NOT NULL CHECK(net_fils=subtotal_fils+shipping_fils-commission_fils), updated_at INTEGER NOT NULL,
 UNIQUE(order_id,store_id)
);
CREATE TABLE order_items (
 id INTEGER PRIMARY KEY, seller_order_id INTEGER NOT NULL REFERENCES seller_orders(id), product_id INTEGER NOT NULL REFERENCES products(id),
 title_ar TEXT NOT NULL, title_en TEXT NOT NULL, unit_price_fils INTEGER NOT NULL, quantity INTEGER NOT NULL CHECK(quantity>0),
 commission_bps INTEGER NOT NULL, commission_fils INTEGER NOT NULL, image_url TEXT NOT NULL, UNIQUE(seller_order_id,product_id)
);
CREATE TABLE order_events (
 id INTEGER PRIMARY KEY, seller_order_id INTEGER NOT NULL REFERENCES seller_orders(id), actor_id INTEGER NOT NULL REFERENCES users(id),
 from_status TEXT, to_status TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL
);
CREATE TABLE inventory_movements (
 id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES products(id), actor_id INTEGER NOT NULL REFERENCES users(id),
 delta INTEGER NOT NULL, reason TEXT NOT NULL, seller_order_id INTEGER REFERENCES seller_orders(id), created_at INTEGER NOT NULL,
 UNIQUE(product_id,seller_order_id,reason)
);
CREATE TABLE ledger (
 id INTEGER PRIMARY KEY, seller_order_id INTEGER NOT NULL REFERENCES seller_orders(id), store_id INTEGER NOT NULL REFERENCES stores(id),
 kind TEXT NOT NULL CHECK(kind='cod_commission_due'), amount_fils INTEGER NOT NULL CHECK(amount_fils>=0),
 created_at INTEGER NOT NULL, UNIQUE(seller_order_id,kind)
);
CREATE TABLE notifications (
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), kind TEXT NOT NULL,
 body_ar TEXT NOT NULL, body_en TEXT NOT NULL, read_at INTEGER, created_at INTEGER NOT NULL
);
CREATE INDEX notifications_user ON notifications(user_id,created_at);
CREATE TABLE audit_log (
 id INTEGER PRIMARY KEY, actor_id INTEGER REFERENCES users(id), action TEXT NOT NULL, entity TEXT NOT NULL,
 entity_id TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL
);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO settings VALUES('commission_bps','500');
INSERT INTO settings VALUES('home_title_ar','كل اللي تحبّه، في مكان واحد.');
INSERT INTO settings VALUES('home_title_en','Good finds. Great beginnings.');
CREATE TABLE mail_outbox (
 id INTEGER PRIMARY KEY, recipient TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL, html TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','sending','sent','file','failed')),
 attempts INTEGER NOT NULL DEFAULT 0, available_at INTEGER NOT NULL, created_at INTEGER NOT NULL,
 sent_at INTEGER, last_error TEXT NOT NULL DEFAULT ''
);
CREATE TRIGGER immutable_audit_update BEFORE UPDATE ON audit_log BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER immutable_audit_delete BEFORE DELETE ON audit_log BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER immutable_ledger_update BEFORE UPDATE ON ledger BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER immutable_ledger_delete BEFORE DELETE ON ledger BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER immutable_events_update BEFORE UPDATE ON order_events BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER immutable_events_delete BEFORE DELETE ON order_events BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER immutable_inventory_update BEFORE UPDATE ON inventory_movements BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER immutable_inventory_delete BEFORE DELETE ON inventory_movements BEGIN SELECT RAISE(ABORT,'append-only'); END;
