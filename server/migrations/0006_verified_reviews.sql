-- Verified-purchase reviews. Eligibility derives from the buyer and delivered order item.
CREATE TABLE reviews (
 id INTEGER PRIMARY KEY,
 order_item_id INTEGER NOT NULL UNIQUE REFERENCES order_items(id) ON DELETE RESTRICT,
 buyer_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
 seller_order_id INTEGER NOT NULL REFERENCES seller_orders(id) ON DELETE RESTRICT,
 rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
 body TEXT NOT NULL DEFAULT '' CHECK(length(body)<=2000),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),
 moderation_note TEXT NOT NULL DEFAULT '',
 moderated_by INTEGER REFERENCES users(id) ON DELETE RESTRICT,
 moderated_at INTEGER,
 created_at INTEGER NOT NULL,
 updated_at INTEGER NOT NULL,
 CHECK((status='pending' AND moderated_by IS NULL AND moderated_at IS NULL)
    OR (status IN ('approved','rejected') AND moderated_by IS NOT NULL AND moderated_at IS NOT NULL))
);
CREATE INDEX reviews_product_public ON reviews(product_id,status,created_at DESC,id DESC);
CREATE INDEX reviews_queue ON reviews(status,created_at,id);

CREATE TABLE review_reports (
 id INTEGER PRIMARY KEY,
 review_id INTEGER NOT NULL REFERENCES reviews(id) ON DELETE RESTRICT,
 reporter_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 reason TEXT NOT NULL CHECK(reason IN ('incorrect','personal_data','abuse','other')),
 details TEXT NOT NULL DEFAULT '' CHECK(length(details)<=500),
 status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved','dismissed')),
 resolution_note TEXT NOT NULL DEFAULT '',
 resolved_by INTEGER REFERENCES users(id) ON DELETE RESTRICT,
 resolved_at INTEGER,
 created_at INTEGER NOT NULL,
 UNIQUE(review_id,reporter_id),
 CHECK((status='open' AND resolved_by IS NULL AND resolved_at IS NULL)
    OR (status IN ('resolved','dismissed') AND resolved_by IS NOT NULL AND resolved_at IS NOT NULL))
);
CREATE INDEX review_reports_queue ON review_reports(status,created_at,id);

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

CREATE TRIGGER immutable_reviews_update
BEFORE UPDATE OF order_item_id,buyer_id,product_id,seller_order_id,rating,body,created_at ON reviews
BEGIN SELECT RAISE(ABORT,'review purchase and content are immutable'); END;
CREATE TRIGGER immutable_reviews_delete
BEFORE DELETE ON reviews
BEGIN SELECT RAISE(ABORT,'review history cannot be deleted'); END;

CREATE TRIGGER review_moderation_authority
BEFORE UPDATE OF status,moderation_note,moderated_by,moderated_at ON reviews
WHEN OLD.status<>'pending'
 OR NEW.status NOT IN ('approved','rejected')
 OR NOT EXISTS (SELECT 1 FROM users u WHERE u.id=NEW.moderated_by AND u.role='admin' AND u.active=1)
BEGIN SELECT RAISE(ABORT,'only an administrator can moderate a pending review'); END;

CREATE TRIGGER review_reports_not_author
BEFORE INSERT ON review_reports
WHEN EXISTS (SELECT 1 FROM reviews r WHERE r.id=NEW.review_id AND r.buyer_id=NEW.reporter_id)
BEGIN SELECT RAISE(ABORT,'review author cannot report own review'); END;
CREATE TRIGGER immutable_review_reports_update
BEFORE UPDATE OF review_id,reporter_id,reason,details,created_at ON review_reports
BEGIN SELECT RAISE(ABORT,'review report evidence is immutable'); END;
CREATE TRIGGER immutable_review_reports_delete
BEFORE DELETE ON review_reports
BEGIN SELECT RAISE(ABORT,'review report history cannot be deleted'); END;

CREATE TRIGGER review_report_moderation_authority
BEFORE UPDATE OF status,resolution_note,resolved_by,resolved_at ON review_reports
WHEN OLD.status<>'open'
 OR NEW.status NOT IN ('resolved','dismissed')
 OR NOT EXISTS (SELECT 1 FROM users u WHERE u.id=NEW.resolved_by AND u.role='admin' AND u.active=1)
BEGIN SELECT RAISE(ABORT,'only an administrator can resolve an open review report'); END;
