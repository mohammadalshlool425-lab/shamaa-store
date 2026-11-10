-- Product variants: one stock-keeping unit per sellable combination.
CREATE TABLE product_variants (
 id INTEGER PRIMARY KEY,
 product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
 store_id INTEGER NOT NULL REFERENCES stores(id),
 sku TEXT NOT NULL,
 attributes_json TEXT NOT NULL,
 price_fils INTEGER NOT NULL CHECK(price_fils BETWEEN 1 AND 1000000000),
 compare_at_fils INTEGER CHECK(compare_at_fils IS NULL OR compare_at_fils>price_fils),
 stock INTEGER NOT NULL CHECK(stock BETWEEN 0 AND 1000000),
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
 version INTEGER NOT NULL DEFAULT 1,
 created_at INTEGER NOT NULL,
 updated_at INTEGER NOT NULL,
 UNIQUE(product_id,sku),
 UNIQUE(store_id,sku),
 UNIQUE(product_id,attributes_json)
);
CREATE INDEX product_variants_active ON product_variants(product_id,active,price_fils);

-- A cart can contain several variants of one parent product. variant_key=0 is
-- the legacy unconfigured product line; variant_key otherwise mirrors variant_id.
ALTER TABLE carts RENAME TO carts_v3;
CREATE TABLE carts (
 user_id INTEGER NOT NULL REFERENCES users(id),
 product_id INTEGER NOT NULL REFERENCES products(id),
 variant_id INTEGER REFERENCES product_variants(id),
 variant_key INTEGER NOT NULL DEFAULT 0,
 quantity INTEGER NOT NULL CHECK(quantity BETWEEN 1 AND 99),
 PRIMARY KEY(user_id,product_id,variant_key),
 CHECK(variant_key=COALESCE(variant_id,0))
);
INSERT INTO carts(user_id,product_id,variant_id,variant_key,quantity)
SELECT user_id,product_id,NULL,0,quantity FROM carts_v3;
DROP TABLE carts_v3;

-- Preserve legacy lines and make the unique key variant-aware.
ALTER TABLE order_items RENAME TO order_items_v3;
CREATE TABLE order_items (
 id INTEGER PRIMARY KEY,
 seller_order_id INTEGER NOT NULL REFERENCES seller_orders(id),
 product_id INTEGER NOT NULL REFERENCES products(id),
 variant_id INTEGER REFERENCES product_variants(id),
 variant_key INTEGER NOT NULL DEFAULT 0,
 variant_sku TEXT NOT NULL DEFAULT '',
 variant_attributes TEXT NOT NULL DEFAULT '{}',
 title_ar TEXT NOT NULL,
 title_en TEXT NOT NULL,
 unit_price_fils INTEGER NOT NULL,
 quantity INTEGER NOT NULL CHECK(quantity>0),
 commission_bps INTEGER NOT NULL,
 commission_fils INTEGER NOT NULL,
 image_url TEXT NOT NULL,
 condition TEXT NOT NULL DEFAULT 'new' CHECK(condition IN ('new','used')),
 condition_note TEXT NOT NULL DEFAULT '',
 UNIQUE(seller_order_id,product_id,variant_key),
 CHECK(variant_key=COALESCE(variant_id,0))
);
INSERT INTO order_items(
 id,seller_order_id,product_id,variant_id,variant_key,variant_sku,variant_attributes,
 title_ar,title_en,unit_price_fils,quantity,commission_bps,commission_fils,image_url,condition,condition_note
)
SELECT id,seller_order_id,product_id,NULL,0,'','{}',
       title_ar,title_en,unit_price_fils,quantity,commission_bps,commission_fils,image_url,condition,condition_note
FROM order_items_v3;
DROP TABLE order_items_v3;
