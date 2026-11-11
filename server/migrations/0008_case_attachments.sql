-- SHAM'A phase 2, batch 6: private shipment-case evidence images.
CREATE TABLE order_case_attachments (
 id INTEGER PRIMARY KEY,
 case_id INTEGER NOT NULL REFERENCES order_cases(id) ON DELETE RESTRICT,
 uploader_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 uploader_role TEXT NOT NULL CHECK(uploader_role IN ('buyer','seller')),
 storage_key TEXT NOT NULL UNIQUE CHECK(length(storage_key)=64 AND storage_key NOT GLOB '*[^0-9a-f]*'),
 content_type TEXT NOT NULL CHECK(content_type='image/webp'),
 bytes INTEGER NOT NULL CHECK(bytes BETWEEN 1 AND 5242880),
 digest TEXT NOT NULL CHECK(length(digest)=64 AND digest NOT GLOB '*[^0-9a-f]*'),
 created_at INTEGER NOT NULL,
 UNIQUE(case_id,digest)
);
CREATE INDEX order_case_attachments_timeline ON order_case_attachments(case_id,id);

CREATE TRIGGER order_case_attachment_authority
BEFORE INSERT ON order_case_attachments
WHEN NOT EXISTS (
 SELECT 1 FROM order_cases c JOIN users u ON u.id=NEW.uploader_id
 WHERE c.id=NEW.case_id AND c.status IN ('open','seller_responded')
   AND u.active=1 AND u.email_verified_at IS NOT NULL
   AND (
     (NEW.uploader_role='buyer' AND c.buyer_id=NEW.uploader_id)
     OR
     (NEW.uploader_role='seller' AND c.seller_id=NEW.uploader_id)
   )
)
BEGIN SELECT RAISE(ABORT,'case attachment uploader is not an active verified participant'); END;

CREATE TRIGGER order_case_attachments_immutable_update
BEFORE UPDATE ON order_case_attachments
BEGIN SELECT RAISE(ABORT,'case attachment evidence is immutable'); END;
CREATE TRIGGER order_case_attachments_immutable_delete
BEFORE DELETE ON order_case_attachments
BEGIN SELECT RAISE(ABORT,'case attachment evidence is immutable'); END;
