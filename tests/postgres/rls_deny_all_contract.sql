-- Test-only proof that FORCE RLS remains closed for a non-owner,
-- non-superuser, NOBYPASSRLS role before any application policies are added.
-- The temporary role receives SELECT only; it is never used by the app.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'shamaa_rls_probe') THEN
    CREATE ROLE shamaa_rls_probe NOLOGIN NOSUPERUSER NOBYPASSRLS;
  END IF;
END
$$;

ALTER ROLE shamaa_rls_probe NOLOGIN NOSUPERUSER NOBYPASSRLS;
GRANT USAGE ON SCHEMA shamaa_app TO shamaa_rls_probe;
GRANT SELECT ON ALL TABLES IN SCHEMA shamaa_app TO shamaa_rls_probe;

SET ROLE shamaa_rls_probe;

DO $$
DECLARE
  table_record RECORD;
  visible_count BIGINT;
  protected_table_count INTEGER := 0;
BEGIN
  IF current_user <> 'shamaa_rls_probe' THEN
    RAISE EXCEPTION 'RLS probe did not assume the restricted role';
  END IF;

  IF EXISTS (
    SELECT 1 FROM pg_roles
    WHERE rolname = current_user AND (rolsuper OR rolbypassrls OR rolcanlogin)
  ) THEN
    RAISE EXCEPTION 'RLS probe role must be NOLOGIN, NOSUPERUSER and NOBYPASSRLS';
  END IF;

  FOR table_record IN
    SELECT c.relname
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'shamaa_app' AND c.relkind IN ('r','p')
    ORDER BY c.relname
  LOOP
    protected_table_count := protected_table_count + 1;

    IF NOT (
      SELECT relrowsecurity AND relforcerowsecurity
      FROM pg_class
      WHERE oid = format('%I.%I', 'shamaa_app', table_record.relname)::regclass
    ) THEN
      RAISE EXCEPTION 'RLS and FORCE RLS must both be enabled on %', table_record.relname;
    END IF;

    EXECUTE format('SELECT count(*) FROM shamaa_app.%I', table_record.relname)
      INTO visible_count;

    IF visible_count <> 0 THEN
      RAISE EXCEPTION 'Deny-all baseline leaked % rows from %',
        visible_count, table_record.relname;
    END IF;
  END LOOP;

  IF protected_table_count <> 32 THEN
    RAISE EXCEPTION 'Expected 32 protected tables, found %', protected_table_count;
  END IF;

  -- Setting a forged-looking user identity or a valid internal label must
  -- not grant access by itself; policies, not GUC values, are the boundary.
  PERFORM set_config('app.current_user_id', '1', false);
  PERFORM set_config('app.system_operation', 'mail.worker', false);

  IF (SELECT count(*) FROM shamaa_app.users) <> 0
     OR (SELECT count(*) FROM shamaa_app.mail_outbox) <> 0
     OR (SELECT count(*) FROM shamaa_app.ledger) <> 0 THEN
    RAISE EXCEPTION 'Context settings alone must never bypass RLS';
  END IF;
END
$$;

RESET ROLE;

REVOKE SELECT ON ALL TABLES IN SCHEMA shamaa_app FROM shamaa_rls_probe;
REVOKE USAGE ON SCHEMA shamaa_app FROM shamaa_rls_probe;
DROP ROLE shamaa_rls_probe;
