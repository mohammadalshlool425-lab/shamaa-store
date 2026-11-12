-- Phase 2 batch 6: private shipment cases and append-only case events.
CREATE TABLE order_cases (
 id INTEGER PRIMARY KEY,
 seller_order_id INTEGER NOT NULL UNIQUE REFERENCES seller_orders(id) ON DELETE RESTRICT,
 buyer_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 seller_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 reason TEXT NOT NULL CHECK(reason IN ('not_received','damaged','wrong_item','missing_item','not_as_described','other')),
 description TEXT NOT NULL CHECK(length(description) BETWEEN 20 AND 3000),
 status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','seller_responded','resolved','rejected')),
 opened_at INTEGER NOT NULL,
 response_due_at INTEGER NOT NULL,
 updated_at INTEGER NOT NULL,
 closed_at INTEGER,
 closed_by INTEGER REFERENCES users(id) ON DELETE RESTRICT,
 resolution_note TEXT NOT NULL DEFAULT '',
 CHECK(response_due_at>=opened_at),
 CHECK(
   (status IN ('open','seller_responded') AND closed_at IS NULL AND closed_by IS NULL AND resolution_note='')
   OR
   (status IN ('resolved','rejected') AND closed_at IS NOT NULL AND closed_by IS NOT NULL AND length(resolution_note)>=5)
 )
);
CREATE INDEX order_cases_status_queue ON order_cases(status,updated_at,id);
CREATE TABLE order_case_events (
 id INTEGER PRIMARY KEY,
 case_id INTEGER NOT NULL REFERENCES order_cases(id) ON DELETE RESTRICT,
 actor_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 actor_role TEXT NOT NULL CHECK(actor_role IN ('buyer','seller','admin')),
 event_type TEXT NOT NULL CHECK(event_type IN ('opened','buyer_message','seller_response','admin_resolved','admin_rejected')),
 body TEXT NOT NULL CHECK(length(body) BETWEEN 1 AND 3000),
 created_at INTEGER NOT NULL
);
CREATE INDEX order_case_events_timeline ON order_case_events(case_id,id);

CREATE TRIGGER order_case_event_authority
BEFORE INSERT ON order_case_events
WHEN NOT (
 (NEW.actor_role='buyer' AND NEW.event_type IN ('opened','buyer_message')
  AND EXISTS (SELECT 1 FROM order_cases c WHERE c.id=NEW.case_id AND c.buyer_id=NEW.actor_id))
 OR
 (NEW.actor_role='seller' AND NEW.event_type='seller_response'
  AND EXISTS (SELECT 1 FROM order_cases c WHERE c.id=NEW.case_id AND c.seller_id=NEW.actor_id))
 OR
 (NEW.actor_role='admin' AND NEW.event_type IN ('admin_resolved','admin_rejected')
  AND EXISTS (SELECT 1 FROM users u WHERE u.id=NEW.actor_id AND u.role='admin' AND u.active=1))
)
BEGIN SELECT RAISE(ABORT,'case event actor is not authorized'); END;

CREATE TRIGGER order_case_events_immutable_update
BEFORE UPDATE ON order_case_events
BEGIN SELECT RAISE(ABORT,'case event history is append-only'); END;
CREATE TRIGGER order_case_events_immutable_delete
BEFORE DELETE ON order_case_events
BEGIN SELECT RAISE(ABORT,'case event history is append-only'); END;

CREATE TRIGGER order_case_content_immutable
BEFORE UPDATE OF seller_order_id,buyer_id,seller_id,reason,description,opened_at,response_due_at ON order_cases
BEGIN SELECT RAISE(ABORT,'case identity and opening evidence are immutable'); END;

CREATE TRIGGER order_case_status_guard
BEFORE UPDATE OF status ON order_cases
WHEN NEW.status<>OLD.status AND NOT (
 (OLD.status='open' AND NEW.status='seller_responded' AND EXISTS (
   SELECT 1 FROM order_case_events e WHERE e.id=(SELECT MAX(id) FROM order_case_events WHERE case_id=OLD.id)
     AND e.case_id=OLD.id AND e.actor_id=OLD.seller_id AND e.actor_role='seller'
     AND e.event_type='seller_response' AND e.created_at=NEW.updated_at
 ))
 OR
 (OLD.status='seller_responded' AND NEW.status='open' AND EXISTS (
   SELECT 1 FROM order_case_events e WHERE e.id=(SELECT MAX(id) FROM order_case_events WHERE case_id=OLD.id)
     AND e.case_id=OLD.id AND e.actor_id=OLD.buyer_id AND e.actor_role='buyer'
     AND e.event_type='buyer_message' AND e.created_at=NEW.updated_at
 ))
 OR
 (OLD.status IN ('open','seller_responded') AND NEW.status IN ('resolved','rejected')
   AND NEW.closed_by IS NOT NULL AND NEW.closed_at=NEW.updated_at
   AND EXISTS (SELECT 1 FROM users u WHERE u.id=NEW.closed_by AND u.role='admin' AND u.active=1)
   AND EXISTS (
     SELECT 1 FROM order_case_events e WHERE e.id=(SELECT MAX(id) FROM order_case_events WHERE case_id=OLD.id)
       AND e.case_id=OLD.id AND e.actor_id=NEW.closed_by AND e.actor_role='admin'
       AND e.event_type=CASE NEW.status WHEN 'resolved' THEN 'admin_resolved' ELSE 'admin_rejected' END
       AND e.created_at=NEW.closed_at AND e.body=NEW.resolution_note
   ))
)
BEGIN SELECT RAISE(ABORT,'invalid case status transition'); END;
