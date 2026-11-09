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
