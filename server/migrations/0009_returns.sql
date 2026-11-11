-- SHAM'A phase 2, batch 7: controlled item-level return requests.
CREATE TABLE return_requests (
 id INTEGER PRIMARY KEY,
 order_item_id INTEGER NOT NULL REFERENCES order_items(id) ON DELETE RESTRICT,
 seller_order_id INTEGER NOT NULL REFERENCES seller_orders(id) ON DELETE RESTRICT,
 buyer_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 seller_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 quantity INTEGER NOT NULL CHECK(quantity BETWEEN 1 AND 99),
 reason TEXT NOT NULL CHECK(reason IN ('damaged','wrong_item','missing_item','not_as_described','changed_mind','other')),
 description TEXT NOT NULL CHECK(length(description) BETWEEN 20 AND 2000),
 status TEXT NOT NULL DEFAULT 'requested'
   CHECK(status IN ('requested','approved','rejected','return_shipped','received','resolved','cancelled')),
 requested_at INTEGER NOT NULL,
 response_due_at INTEGER NOT NULL,
 updated_at INTEGER NOT NULL,
 seller_note TEXT NOT NULL DEFAULT '',
 return_carrier TEXT NOT NULL DEFAULT '',
 return_tracking_number TEXT NOT NULL DEFAULT '',
 closed_at INTEGER,
 closed_by INTEGER REFERENCES users(id) ON DELETE RESTRICT,
 decision_note TEXT NOT NULL DEFAULT '',
 CHECK(response_due_at>=requested_at),
 CHECK(
   (return_carrier='' AND return_tracking_number='')
   OR (length(return_carrier) BETWEEN 2 AND 50 AND length(return_tracking_number) BETWEEN 3 AND 100)
 ),
 CHECK(
   (status IN ('return_shipped','received','resolved') AND return_carrier<>'' AND return_tracking_number<>'')
   OR status NOT IN ('return_shipped','received','resolved')
 ),
 CHECK(
   (status IN ('rejected','resolved','cancelled') AND closed_at IS NOT NULL AND closed_by IS NOT NULL AND length(decision_note)>=5)
   OR
   (status NOT IN ('rejected','resolved','cancelled') AND closed_at IS NULL AND closed_by IS NULL AND decision_note='')
 )
);
CREATE INDEX return_requests_buyer_queue ON return_requests(buyer_id,status,updated_at,id);
CREATE INDEX return_requests_seller_queue ON return_requests(seller_id,status,updated_at,id);
CREATE INDEX return_requests_item_history ON return_requests(order_item_id,id);
CREATE UNIQUE INDEX one_active_return_per_order_item ON return_requests(order_item_id)
 WHERE status IN ('requested','approved','return_shipped','received');

CREATE TABLE return_request_events (
 id INTEGER PRIMARY KEY,
 request_id INTEGER NOT NULL REFERENCES return_requests(id) ON DELETE RESTRICT,
 actor_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 actor_role TEXT NOT NULL CHECK(actor_role IN ('buyer','seller','admin')),
 event_type TEXT NOT NULL CHECK(event_type IN (
   'opened','seller_approved','seller_rejected','buyer_cancelled',
   'buyer_return_shipped','seller_received','admin_resolved','admin_rejected'
 )),
 body TEXT NOT NULL CHECK(length(body) BETWEEN 1 AND 2000),
 created_at INTEGER NOT NULL
);
CREATE INDEX return_request_events_timeline ON return_request_events(request_id,id);

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

CREATE TRIGGER return_request_event_authority
BEFORE INSERT ON return_request_events
WHEN NOT (
 (NEW.actor_role='buyer' AND NEW.event_type IN ('opened','buyer_cancelled','buyer_return_shipped')
  AND EXISTS (
    SELECT 1 FROM return_requests r WHERE r.id=NEW.request_id AND r.buyer_id=NEW.actor_id
      AND ((NEW.event_type='opened' AND r.status='requested')
        OR (NEW.event_type='buyer_cancelled' AND r.status IN ('requested','approved'))
        OR (NEW.event_type='buyer_return_shipped' AND r.status='approved'))
  ))
 OR
 (NEW.actor_role='seller' AND NEW.event_type IN ('seller_approved','seller_rejected','seller_received')
  AND EXISTS (
    SELECT 1 FROM return_requests r WHERE r.id=NEW.request_id AND r.seller_id=NEW.actor_id
      AND ((NEW.event_type IN ('seller_approved','seller_rejected') AND r.status='requested')
        OR (NEW.event_type='seller_received' AND r.status='return_shipped'))
  ))
 OR
 (NEW.actor_role='admin' AND NEW.event_type IN ('admin_resolved','admin_rejected')
  AND EXISTS (SELECT 1 FROM users u WHERE u.id=NEW.actor_id AND u.role='admin' AND u.active=1))
)
BEGIN SELECT RAISE(ABORT,'return event actor is not authorized'); END;

CREATE TRIGGER return_request_events_immutable_update
BEFORE UPDATE ON return_request_events
BEGIN SELECT RAISE(ABORT,'return request event history is append-only'); END;
CREATE TRIGGER return_request_events_immutable_delete
BEFORE DELETE ON return_request_events
BEGIN SELECT RAISE(ABORT,'return request event history is append-only'); END;

CREATE TRIGGER return_request_identity_immutable
BEFORE UPDATE OF order_item_id,seller_order_id,buyer_id,seller_id,quantity,reason,description,requested_at,response_due_at
ON return_requests
BEGIN SELECT RAISE(ABORT,'return request identity and opening evidence are immutable'); END;

CREATE TRIGGER return_request_seller_note_guard
BEFORE UPDATE OF seller_note ON return_requests
WHEN NEW.seller_note<>OLD.seller_note
 AND NOT (OLD.status='requested' AND NEW.status IN ('approved','rejected'))
BEGIN SELECT RAISE(ABORT,'seller response note can only be set with the seller decision'); END;

CREATE TRIGGER return_request_tracking_guard
BEFORE UPDATE OF return_carrier,return_tracking_number ON return_requests
WHEN (NEW.return_carrier<>OLD.return_carrier OR NEW.return_tracking_number<>OLD.return_tracking_number)
 AND NOT (OLD.status='approved' AND NEW.status='return_shipped'
          AND OLD.return_carrier='' AND OLD.return_tracking_number=''
          AND NEW.return_carrier<>'' AND NEW.return_tracking_number<>'')
BEGIN SELECT RAISE(ABORT,'return tracking can only be set when the buyer ships the return'); END;

CREATE TRIGGER return_request_status_guard
BEFORE UPDATE OF status ON return_requests
WHEN NEW.status<>OLD.status AND NOT (
 (OLD.status='requested' AND NEW.status='approved' AND EXISTS (
   SELECT 1 FROM return_request_events e WHERE e.id=(SELECT MAX(id) FROM return_request_events WHERE request_id=OLD.id)
    AND e.request_id=OLD.id AND e.actor_id=OLD.seller_id AND e.actor_role='seller'
    AND e.event_type='seller_approved' AND e.created_at=NEW.updated_at
 ))
 OR
 (OLD.status='requested' AND NEW.status='rejected' AND NEW.closed_by=OLD.seller_id
   AND NEW.closed_at=NEW.updated_at AND length(NEW.decision_note)>=5 AND EXISTS (
   SELECT 1 FROM return_request_events e WHERE e.id=(SELECT MAX(id) FROM return_request_events WHERE request_id=OLD.id)
    AND e.request_id=OLD.id AND e.actor_id=OLD.seller_id AND e.actor_role='seller'
    AND e.event_type='seller_rejected' AND e.created_at=NEW.closed_at AND e.body=NEW.decision_note
 ))
 OR
 (OLD.status IN ('requested','approved') AND NEW.status='cancelled' AND NEW.closed_by=OLD.buyer_id
   AND NEW.closed_at=NEW.updated_at AND length(NEW.decision_note)>=5 AND EXISTS (
   SELECT 1 FROM return_request_events e WHERE e.id=(SELECT MAX(id) FROM return_request_events WHERE request_id=OLD.id)
    AND e.request_id=OLD.id AND e.actor_id=OLD.buyer_id AND e.actor_role='buyer'
    AND e.event_type='buyer_cancelled' AND e.created_at=NEW.closed_at AND e.body=NEW.decision_note
 ))
 OR
 (OLD.status='approved' AND NEW.status='return_shipped' AND NEW.closed_at IS NULL
   AND NEW.return_carrier<>'' AND NEW.return_tracking_number<>'' AND EXISTS (
   SELECT 1 FROM return_request_events e WHERE e.id=(SELECT MAX(id) FROM return_request_events WHERE request_id=OLD.id)
    AND e.request_id=OLD.id AND e.actor_id=OLD.buyer_id AND e.actor_role='buyer'
    AND e.event_type='buyer_return_shipped' AND e.created_at=NEW.updated_at
 ))
 OR
 (OLD.status='return_shipped' AND NEW.status='received' AND EXISTS (
   SELECT 1 FROM return_request_events e WHERE e.id=(SELECT MAX(id) FROM return_request_events WHERE request_id=OLD.id)
    AND e.request_id=OLD.id AND e.actor_id=OLD.seller_id AND e.actor_role='seller'
    AND e.event_type='seller_received' AND e.created_at=NEW.updated_at
 ))
 OR
 (OLD.status IN ('requested','approved','return_shipped','received') AND NEW.status IN ('resolved','rejected')
   AND NEW.closed_by IS NOT NULL AND NEW.closed_at=NEW.updated_at AND length(NEW.decision_note)>=5
   AND EXISTS (SELECT 1 FROM users u WHERE u.id=NEW.closed_by AND u.role='admin' AND u.active=1)
   AND EXISTS (
    SELECT 1 FROM return_request_events e WHERE e.id=(SELECT MAX(id) FROM return_request_events WHERE request_id=OLD.id)
     AND e.request_id=OLD.id AND e.actor_id=NEW.closed_by AND e.actor_role='admin'
     AND e.event_type=CASE NEW.status WHEN 'resolved' THEN 'admin_resolved' ELSE 'admin_rejected' END
     AND e.created_at=NEW.closed_at AND e.body=NEW.decision_note
   ))
)
BEGIN SELECT RAISE(ABORT,'invalid return request status transition'); END;
