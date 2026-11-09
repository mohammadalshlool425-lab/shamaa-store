-- Schema contract checks for the isolated SHAM'A PostgreSQL foundation.
\set ON_ERROR_STOP on

DO $$
DECLARE
  table_count integer;
  rls_count integer;
  forced_count integer;
  policy_count integer;
  trigger_count integer;
BEGIN
  SELECT count(*), count(*) FILTER (WHERE c.relrowsecurity),
         count(*) FILTER (WHERE c.relforcerowsecurity)
    INTO table_count, rls_count, forced_count
    FROM pg_class c
    JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='shamaa_app' AND c.relkind='r';

  IF table_count <> 19 OR rls_count <> 19 OR forced_count <> 19 THEN
    RAISE EXCEPTION 'Expected 19 tables with ENABLE and FORCE RLS; got tables %, enabled %, forced %',
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
      ('mail_outbox','created_at'),('mail_outbox','sent_at'),('mail_outbox','last_error')
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
