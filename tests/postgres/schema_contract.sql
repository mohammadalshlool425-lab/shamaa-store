-- Schema contract checks for the isolated SHAM'A PostgreSQL foundation.
\set ON_ERROR_STOP on

DO $$
DECLARE
  table_count integer;
  rls_count integer;
  forced_count integer;
  policy_count integer;
  trigger_count integer;
  message_guard_count integer;
  review_guard_count integer;
  case_guard_count integer;
  case_attachment_guard_count integer;
  return_guard_count integer;
BEGIN
  SELECT count(*), count(*) FILTER (WHERE c.relrowsecurity),
         count(*) FILTER (WHERE c.relforcerowsecurity)
    INTO table_count, rls_count, forced_count
    FROM pg_class c
    JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='shamaa_app' AND c.relkind='r';

  IF table_count <> 30 OR rls_count <> 30 OR forced_count <> 30 THEN
    RAISE EXCEPTION 'Expected 30 tables with ENABLE and FORCE RLS; got tables %, enabled %, forced %',
      table_count, rls_count, forced_count;
  END IF;

  SELECT count(*) INTO policy_count
    FROM pg_policies WHERE schemaname='shamaa_app';
  IF policy_count <> 0 THEN
    RAISE EXCEPTION 'Foundation migration must remain closed until reviewed policies are installed; found % policies', policy_count;
  END IF;

  SELECT count(*) INTO trigger_count
    FROM pg_trigger t
    JOIN pg_class c ON c.oid=t.tgrelid
    JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='shamaa_app' AND NOT t.tgisinternal
     AND t.tgname IN ('immutable_audit_update','immutable_ledger_update',
                      'immutable_events_update','immutable_inventory_update');
  IF trigger_count <> 4 THEN
    RAISE EXCEPTION 'Expected four append-only guards; got %', trigger_count;
  END IF;

  SELECT count(*) INTO message_guard_count
    FROM pg_trigger t
    JOIN pg_class c ON c.oid=t.tgrelid
    JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='shamaa_app' AND NOT t.tgisinternal
     AND t.tgname IN ('messages_sender_participant','immutable_messages_update');
  IF message_guard_count <> 2 THEN
    RAISE EXCEPTION 'Expected participant and append-only message guards; got %', message_guard_count;
  END IF;
  SELECT count(*) INTO review_guard_count
    FROM pg_trigger t
    JOIN pg_class c ON c.oid=t.tgrelid
    JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='shamaa_app' AND NOT t.tgisinternal
     AND t.tgname IN ('review_purchase_eligibility','review_content_immutable',
                      'review_moderation_authority','review_report_not_author',
                      'review_report_content_immutable','review_report_moderation_authority');
  IF review_guard_count <> 6 THEN
    RAISE EXCEPTION 'Expected six verified-review and report guards; got %', review_guard_count;
  END IF;
  SELECT count(*) INTO case_guard_count
    FROM pg_trigger t
    JOIN pg_class c ON c.oid=t.tgrelid
    JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='shamaa_app' AND NOT t.tgisinternal
     AND t.tgname IN ('order_case_event_authority','order_case_events_immutable_update',
                      'order_case_events_immutable_delete','order_case_content_immutable',
                      'order_case_status_guard');
  IF case_guard_count <> 5 THEN
    RAISE EXCEPTION 'Expected five shipment-case security guards; got %', case_guard_count;
  END IF;
  SELECT count(*) INTO case_attachment_guard_count
    FROM pg_trigger t
    JOIN pg_class c ON c.oid=t.tgrelid
    JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='shamaa_app' AND NOT t.tgisinternal
     AND t.tgname IN ('order_case_attachment_authority',
                      'order_case_attachments_immutable_update',
                      'order_case_attachments_immutable_delete');
  IF case_attachment_guard_count <> 3 THEN
    RAISE EXCEPTION 'Expected three case-attachment guards; got %', case_attachment_guard_count;
  END IF;
  SELECT count(*) INTO return_guard_count
    FROM pg_trigger t
    JOIN pg_class c ON c.oid=t.tgrelid
    JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='shamaa_app' AND NOT t.tgisinternal
     AND t.tgname IN (
       'return_request_event_authority','return_request_events_immutable_update',
       'return_request_events_immutable_delete','return_request_identity_immutable',
       'return_request_seller_note_guard','return_request_tracking_guard',
       'return_request_status_guard'
     );
  IF return_guard_count <> 10 THEN
    RAISE EXCEPTION 'Expected ten return-request guards; got %', return_guard_count;
  END IF;
END $$;

-- Validate critical monetary and identity constraints with rollback-only fixtures.
BEGIN;
INSERT INTO shamaa_app.users(name,email,password_hash,created_at)
VALUES ('CI fixture','ci@example.invalid','not-a-real-password-hash',1);
INSERT INTO shamaa_app.stores(owner_id,slug,name_ar,name_en,city,created_at)
SELECT id,'ci-fixture','اختبار','CI fixture','Irbid',1
FROM shamaa_app.users WHERE email='ci@example.invalid';
INSERT INTO shamaa_app.categories(id,name_ar,name_en,icon)
VALUES ('ci-fixture','اختبار','CI fixture','test');
INSERT INTO shamaa_app.products(store_id,category_id,title_ar,title_en,description_ar,description_en,
 price_fils,stock,sku,created_at,updated_at)
SELECT s.id,'ci-fixture','اختبار','CI fixture','وصف','Description',1000,1,'CI-1',1,1
FROM shamaa_app.stores s WHERE s.slug='ci-fixture';
ROLLBACK;

SELECT 'SHAM''A PostgreSQL schema contract: PASS' AS result;


-- Keep the PostgreSQL foundation aligned with the current SQLite application
-- contract. Missing columns fail CI before a future adapter can be switched on.
DO $$
DECLARE
  expected record;
BEGIN
  FOR expected IN
    SELECT * FROM (VALUES
      ('users','id'),('users','name'),('users','email'),('users','password_hash'),('users','role'),
      ('users','locale'),('users','active'),('users','email_verified_at'),('users','mfa_secret'),
      ('users','mfa_last_step'),('users','created_at'),
      ('sessions','token_hash'),('sessions','user_id'),('sessions','csrf_token'),('sessions','created_at'),
      ('sessions','expires_at'),('sessions','ip_hash'),('sessions','user_agent'),('sessions','mfa_at'),
      ('auth_tokens','token_hash'),('auth_tokens','user_id'),('auth_tokens','purpose'),('auth_tokens','expires_at'),('auth_tokens','used_at'),
      ('rate_limits','bucket'),('rate_limits','window_start'),('rate_limits','count'),
      ('categories','id'),('categories','name_ar'),('categories','name_en'),('categories','icon'),('categories','parent_id'),('categories','commission_bps'),
      ('stores','id'),('stores','owner_id'),('stores','slug'),('stores','name_ar'),('stores','name_en'),
      ('stores','description_ar'),('stores','description_en'),('stores','city'),('stores','accent'),
      ('stores','shipping_fils'),('stores','delivery_days'),('stores','status'),('stores','review_note'),
      ('stores','commission_bps'),('stores','is_demo'),('stores','created_at'),
      ('media','id'),('media','owner_id'),('media','digest'),('media','storage_key'),('media','bytes'),('media','created_at'),
      ('products','id'),('products','store_id'),('products','category_id'),('products','title_ar'),('products','title_en'),
      ('products','description_ar'),('products','description_en'),('products','price_fils'),('products','compare_at_fils'),
      ('products','stock'),('products','sku'),('products','media_id'),('products','demo_image'),('products','status'),
      ('products','review_note'),('products','featured'),('products','version'),('products','created_at'),('products','updated_at'),
      ('carts','user_id'),('carts','product_id'),('carts','quantity'),
      ('orders','id'),('orders','number'),('orders','buyer_id'),('orders','idempotency_key'),('orders','request_hash'),
      ('orders','recipient'),('orders','phone'),('orders','city'),('orders','address'),('orders','subtotal_fils'),
      ('orders','shipping_fils'),('orders','total_fils'),('orders','payment_method'),('orders','is_demo'),('orders','created_at'),
      ('seller_orders','id'),('seller_orders','order_id'),('seller_orders','store_id'),('seller_orders','status'),
      ('seller_orders','subtotal_fils'),('seller_orders','shipping_fils'),('seller_orders','commission_fils'),
      ('seller_orders','net_fils'),('seller_orders','updated_at'),
      ('order_items','id'),('order_items','seller_order_id'),('order_items','product_id'),('order_items','title_ar'),
      ('order_items','title_en'),('order_items','unit_price_fils'),('order_items','quantity'),('order_items','commission_bps'),
      ('order_items','commission_fils'),('order_items','image_url'),
      ('order_events','id'),('order_events','seller_order_id'),('order_events','actor_id'),('order_events','from_status'),
      ('order_events','to_status'),('order_events','note'),('order_events','created_at'),
      ('inventory_movements','id'),('inventory_movements','product_id'),('inventory_movements','actor_id'),
      ('inventory_movements','delta'),('inventory_movements','reason'),('inventory_movements','seller_order_id'),('inventory_movements','created_at'),
      ('ledger','id'),('ledger','seller_order_id'),('ledger','store_id'),('ledger','kind'),('ledger','amount_fils'),('ledger','created_at'),
      ('notifications','id'),('notifications','user_id'),('notifications','kind'),('notifications','body_ar'),
      ('notifications','body_en'),('notifications','read_at'),('notifications','created_at'),
      ('audit_log','id'),('audit_log','actor_id'),('audit_log','action'),('audit_log','entity'),
      ('audit_log','entity_id'),('audit_log','detail'),('audit_log','created_at'),
      ('settings','key'),('settings','value'),
      ('mail_outbox','id'),('mail_outbox','recipient'),('mail_outbox','subject'),('mail_outbox','body'),
      ('mail_outbox','html'),('mail_outbox','status'),('mail_outbox','attempts'),('mail_outbox','available_at'),
      ('mail_outbox','created_at'),('mail_outbox','sent_at'),('mail_outbox','last_error'),
      ('reviews','id'),('reviews','order_item_id'),('reviews','buyer_id'),('reviews','product_id'),
      ('reviews','seller_order_id'),('reviews','rating'),('reviews','body'),('reviews','status'),
      ('reviews','moderation_note'),('reviews','moderated_by'),('reviews','moderated_at'),
      ('reviews','created_at'),('reviews','updated_at'),
      ('review_reports','id'),('review_reports','review_id'),('review_reports','reporter_id'),
      ('review_reports','reason'),('review_reports','details'),('review_reports','status'),
      ('review_reports','resolution_note'),('review_reports','resolved_by'),
      ('review_reports','resolved_at'),('review_reports','created_at'),
      ('order_case_attachments','id'),('order_case_attachments','case_id'),
      ('order_case_attachments','uploader_id'),('order_case_attachments','uploader_role'),
      ('order_case_attachments','storage_key'),('order_case_attachments','content_type'),
      ('order_case_attachments','bytes'),('order_case_attachments','digest'),
      ('order_case_attachments','created_at')
    ) AS contract(table_name,column_name)
  LOOP
    IF NOT EXISTS (
      SELECT 1 FROM information_schema.columns
       WHERE table_schema='shamaa_app'
         AND table_name=expected.table_name
         AND column_name=expected.column_name
    ) THEN
      RAISE EXCEPTION 'PostgreSQL foundation is missing shamaa_app.%.%',
        expected.table_name, expected.column_name;
    END IF;
  END LOOP;
END $$;


-- Exercise negative paths as well as the happy-path fixtures. Every attempted
-- invalid write is isolated in a PL/pgSQL subtransaction and must be rejected.
BEGIN;
INSERT INTO shamaa_app.users(name,email,password_hash,created_at)
VALUES ('Constraint fixture','constraints@example.invalid','not-a-real-password-hash',2);
INSERT INTO shamaa_app.stores(owner_id,slug,name_ar,name_en,city,created_at)
SELECT id,'constraint-fixture','اختبار القيود','Constraint fixture','Irbid',2
FROM shamaa_app.users WHERE email='constraints@example.invalid';
INSERT INTO shamaa_app.categories(id,name_ar,name_en,icon)
VALUES ('constraint-fixture','اختبار القيود','Constraint fixture','test');
INSERT INTO shamaa_app.products(store_id,category_id,title_ar,title_en,description_ar,description_en,
 price_fils,stock,sku,created_at,updated_at)
SELECT s.id,'constraint-fixture','منتج','Product','وصف','Description',1000,2,'VALID-1',2,2
FROM shamaa_app.stores s WHERE s.slug='constraint-fixture';

DO $$
DECLARE
  rejected boolean;
  fixture_user_id bigint;
  fixture_product_id bigint;
  fixture_store_id bigint;
  fixture_audit_id bigint;
  affected integer;
BEGIN
  SELECT id INTO fixture_user_id FROM shamaa_app.users WHERE email='constraints@example.invalid';
  SELECT id INTO fixture_store_id FROM shamaa_app.stores WHERE slug='constraint-fixture';
  SELECT id INTO fixture_product_id FROM shamaa_app.products WHERE sku='VALID-1';

  -- Match the application's atomic stock-decrement predicate. PostgreSQL must
  -- report one affected row when stock is sufficient and zero when it is not.
  UPDATE shamaa_app.products
     SET stock=stock-2
   WHERE id=fixture_product_id AND stock>=2;
  GET DIAGNOSTICS affected = ROW_COUNT;
  IF affected <> 1 THEN RAISE EXCEPTION 'Conditional stock decrement should affect exactly one row'; END IF;

  UPDATE shamaa_app.products
     SET stock=stock-1
   WHERE id=fixture_product_id AND stock>=1;
  GET DIAGNOSTICS affected = ROW_COUNT;
  IF affected <> 0 THEN RAISE EXCEPTION 'Conditional stock decrement must reject overselling'; END IF;
  IF (SELECT stock FROM shamaa_app.products WHERE id=fixture_product_id) <> 0 THEN
    RAISE EXCEPTION 'Conditional stock decrement produced an unexpected stock balance';
  END IF;

  rejected := false;
  BEGIN
    INSERT INTO shamaa_app.users(name,email,password_hash,created_at)
    VALUES ('Duplicate','CONSTRAINTS@example.invalid','not-a-real-password-hash',3);
  EXCEPTION WHEN unique_violation THEN rejected := true;
  END;
  IF NOT rejected THEN RAISE EXCEPTION 'Case-insensitive unique email constraint was not enforced'; END IF;

  rejected := false;
  BEGIN
    INSERT INTO shamaa_app.products(store_id,category_id,title_ar,title_en,description_ar,description_en,
      price_fils,stock,sku,created_at,updated_at)
    VALUES (fixture_store_id,'constraint-fixture','سيئ','Invalid','وصف','Description',1000,-1,'BAD-STOCK',3,3);
  EXCEPTION WHEN check_violation THEN rejected := true;
  END;
  IF NOT rejected THEN RAISE EXCEPTION 'Negative product stock was accepted'; END IF;

  rejected := false;
  BEGIN
    INSERT INTO shamaa_app.carts(user_id,product_id,quantity)
    VALUES (fixture_user_id,fixture_product_id,0);
  EXCEPTION WHEN check_violation THEN rejected := true;
  END;
  IF NOT rejected THEN RAISE EXCEPTION 'Invalid cart quantity was accepted'; END IF;

  rejected := false;
  BEGIN
    INSERT INTO shamaa_app.orders(number,buyer_id,idempotency_key,request_hash,recipient,phone,city,address,
      subtotal_fils,shipping_fils,total_fils,payment_method,created_at)
    VALUES ('BAD-TOTAL',fixture_user_id,'bad-total','hash','Buyer','0790000000','Irbid','Test address',
      1000,1000,2500,'cod',3);
  EXCEPTION WHEN check_violation THEN rejected := true;
  END;
  IF NOT rejected THEN RAISE EXCEPTION 'Inconsistent order total was accepted'; END IF;

  INSERT INTO shamaa_app.audit_log(actor_id,action,entity,entity_id,created_at)
  VALUES (fixture_user_id,'ci_contract_test','user',fixture_user_id::text,3)
  RETURNING id INTO fixture_audit_id;

  rejected := false;
  BEGIN
    UPDATE shamaa_app.audit_log SET detail='tampered' WHERE id=fixture_audit_id;
  EXCEPTION WHEN raise_exception THEN rejected := true;
  END;
  IF NOT rejected THEN RAISE EXCEPTION 'Audit log UPDATE was not rejected'; END IF;

  rejected := false;
  BEGIN
    DELETE FROM shamaa_app.audit_log WHERE id=fixture_audit_id;
  EXCEPTION WHEN raise_exception THEN rejected := true;
  END;
  IF NOT rejected THEN RAISE EXCEPTION 'Audit log DELETE was not rejected'; END IF;
END $$;
ROLLBACK;

SELECT 'SHAM''A PostgreSQL negative-constraint contract: PASS' AS result;

-- Phase 2 batch 1: condition fields exist on listings and immutable order snapshots.
DO $$
DECLARE
  condition_columns integer;
BEGIN
  SELECT count(*) INTO condition_columns
    FROM information_schema.columns
   WHERE table_schema='shamaa_app'
     AND (
       (table_name='products' AND column_name IN ('condition','condition_note'))
       OR
       (table_name='order_items' AND column_name IN ('condition','condition_note'))
     );
  IF condition_columns <> 4 THEN
    RAISE EXCEPTION 'Expected condition and condition_note on products and order_items; found % columns', condition_columns;
  END IF;
END $$;

-- Phase 2 batch 2: the image gallery remains inaccessible until a future reviewed policy.
DO $$
DECLARE
  gallery_rls boolean;
  gallery_force boolean;
  gallery_policy_count integer;
  gallery_public_grant boolean;
BEGIN
  SELECT c.relrowsecurity,c.relforcerowsecurity
    INTO gallery_rls,gallery_force
    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='shamaa_app' AND c.relname='product_images';
  IF gallery_rls IS DISTINCT FROM true OR gallery_force IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'Product gallery must have ENABLE and FORCE RLS';
  END IF;
  SELECT count(*) INTO gallery_policy_count
    FROM pg_policies WHERE schemaname='shamaa_app' AND tablename='product_images';
  IF gallery_policy_count <> 0 THEN
    RAISE EXCEPTION 'Product gallery must remain closed until reviewed RLS policies are installed';
  END IF;
  SELECT EXISTS (
    SELECT 1 FROM pg_class c, LATERAL aclexplode(COALESCE(c.relacl, acldefault('r',c.relowner))) acl
     WHERE c.oid='shamaa_app.product_images'::regclass
       AND acl.grantee=0
  ) INTO gallery_public_grant;
  IF gallery_public_grant THEN
    RAISE EXCEPTION 'Product gallery must not have PUBLIC table grants';
  END IF;
END $$;

-- Phase 2 batch 3: variants are server-only data under FORCE RLS and no policies.
DO $$
DECLARE
  variants_rls boolean;
  variants_force boolean;
  variants_policy_count integer;
  variant_columns integer;
BEGIN
  SELECT c.relrowsecurity,c.relforcerowsecurity
    INTO variants_rls,variants_force
    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='shamaa_app' AND c.relname='product_variants';
  IF variants_rls IS DISTINCT FROM true OR variants_force IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'Product variants must have ENABLE and FORCE RLS';
  END IF;
  SELECT count(*) INTO variants_policy_count
    FROM pg_policies WHERE schemaname='shamaa_app' AND tablename='product_variants';
  IF variants_policy_count <> 0 THEN
    RAISE EXCEPTION 'Product variants must remain private until reviewed RLS policies exist';
  END IF;
  SELECT count(*) INTO variant_columns
    FROM information_schema.columns
   WHERE table_schema='shamaa_app'
     AND ((table_name='carts' AND column_name IN ('variant_id','variant_key'))
       OR (table_name='order_items' AND column_name IN ('variant_id','variant_key','variant_sku','variant_attributes')));
  IF variant_columns <> 6 THEN
    RAISE EXCEPTION 'Expected six variant-aware cart/order columns; found %',variant_columns;
  END IF;
END $$;

DO $$
DECLARE case_columns integer; attachment_columns integer; return_columns integer;
BEGIN
 SELECT count(*) INTO case_columns
   FROM information_schema.columns
  WHERE table_schema='shamaa_app'
    AND (
      (table_name='order_cases' AND column_name IN (
        'id','seller_order_id','buyer_id','seller_id','reason','description','status',
        'opened_at','response_due_at','updated_at','closed_at','closed_by','resolution_note'
      ))
      OR
      (table_name='order_case_events' AND column_name IN (
        'id','case_id','actor_id','actor_role','event_type','body','created_at'
      ))
    );
 IF case_columns <> 20 THEN
   RAISE EXCEPTION 'Expected 20 case and event columns; found %',case_columns;
 END IF;
 SELECT count(*) INTO attachment_columns
   FROM information_schema.columns
  WHERE table_schema='shamaa_app' AND table_name='order_case_attachments'
    AND column_name IN (
      'id','case_id','uploader_id','uploader_role','storage_key',
      'content_type','bytes','digest','created_at'
    );
 IF attachment_columns <> 9 THEN
   RAISE EXCEPTION 'Expected nine case-attachment metadata columns; found %',attachment_columns;
 END IF;
 SELECT count(*) INTO return_columns
   FROM information_schema.columns
  WHERE table_schema='shamaa_app'
    AND (
      (table_name='return_requests' AND column_name IN (
        'id','order_item_id','seller_order_id','buyer_id','seller_id','quantity',
        'reason','description','status','requested_at','response_due_at','updated_at',
        'seller_note','return_carrier','return_tracking_number','closed_at','closed_by','decision_note'
      ))
      OR
      (table_name='return_request_events' AND column_name IN (
        'id','request_id','actor_id','actor_role','event_type','body','created_at'
      ))
    );
 IF return_columns <> 25 THEN
   RAISE EXCEPTION 'Expected 25 return-request columns; found %',return_columns;
 END IF;

 IF NOT EXISTS (
   SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='shamaa_app' AND c.relname='order_cases'
      AND c.relrowsecurity AND c.relforcerowsecurity
 ) OR NOT EXISTS (
   SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='shamaa_app' AND c.relname='order_case_events'
      AND c.relrowsecurity AND c.relforcerowsecurity
 ) OR NOT EXISTS (
   SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='shamaa_app' AND c.relname='order_case_attachments'
      AND c.relrowsecurity AND c.relforcerowsecurity
 ) OR NOT EXISTS (
   SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='shamaa_app' AND c.relname='return_requests'
      AND c.relrowsecurity AND c.relforcerowsecurity
 ) OR NOT EXISTS (
   SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='shamaa_app' AND c.relname='return_request_events'
      AND c.relrowsecurity AND c.relforcerowsecurity
 ) THEN
   RAISE EXCEPTION 'Case, attachment and return tables must enforce row-level security';
 END IF;

 IF EXISTS (
   SELECT 1 FROM pg_policies WHERE schemaname='shamaa_app'
     AND tablename IN ('order_cases','order_case_events','order_case_attachments','return_requests','return_request_events')
 ) THEN
   RAISE EXCEPTION 'Private case and return tables must remain closed until reviewed runtime policies exist';
 END IF;
END $$;
