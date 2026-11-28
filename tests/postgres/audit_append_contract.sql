-- Test-only contract for actor-attributed audit appends.
-- The policy is temporary and rolls back; this is NOT an application migration.
\set ON_ERROR_STOP on

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'shamaa_audit_probe') THEN
    CREATE ROLE shamaa_audit_probe NOLOGIN NOSUPERUSER NOBYPASSRLS;
  END IF;
END
$$;
ALTER ROLE shamaa_audit_probe NOLOGIN NOSUPERUSER NOBYPASSRLS;
GRANT USAGE ON SCHEMA shamaa_app TO shamaa_audit_probe;
GRANT INSERT, SELECT ON shamaa_app.audit_log TO shamaa_audit_probe;
GRANT USAGE ON SEQUENCE shamaa_app.audit_log_id_seq TO shamaa_audit_probe;

BEGIN;

INSERT INTO shamaa_app.users(name,email,password_hash,created_at)
VALUES
  ('Audit actor one','audit-actor-one@example.invalid','not-a-password',1),
  ('Audit actor two','audit-actor-two@example.invalid','not-a-password',1);

-- Pass fixture ids through transaction-local settings so the probe role
-- needs no access to the users table.
SELECT set_config('app.audit_actor_one',
  (SELECT id::text FROM shamaa_app.users WHERE email='audit-actor-one@example.invalid'), TRUE);
SELECT set_config('app.audit_actor_two',
  (SELECT id::text FROM shamaa_app.users WHERE email='audit-actor-two@example.invalid'), TRUE);

CREATE POLICY audit_actor_append_probe ON shamaa_app.audit_log
  FOR INSERT TO shamaa_audit_probe
  WITH CHECK (
    actor_id = NULLIF(current_setting('app.current_user_id', TRUE), '')::bigint
  );

SET ROLE shamaa_audit_probe;

DO $$
DECLARE
  actor_one BIGINT := NULLIF(current_setting('app.audit_actor_one', TRUE), '')::bigint;
  actor_two BIGINT := NULLIF(current_setting('app.audit_actor_two', TRUE), '')::bigint;
  visible_rows BIGINT;
BEGIN
  IF current_user <> 'shamaa_audit_probe' THEN
    RAISE EXCEPTION 'Audit probe did not assume the restricted role';
  END IF;

  IF EXISTS (
    SELECT 1 FROM pg_roles
    WHERE rolname = current_user AND (rolsuper OR rolbypassrls OR rolcanlogin)
  ) THEN
    RAISE EXCEPTION 'Audit probe must be NOLOGIN, NOSUPERUSER and NOBYPASSRLS';
  END IF;

  PERFORM set_config('app.current_user_id', actor_one::text, TRUE);
  INSERT INTO shamaa_app.audit_log(actor_id,action,entity,entity_id,detail,created_at)
  VALUES(actor_one,'probe_append','user',actor_one::text,'restricted-role test',1);

  -- A mismatched actor must be rejected by WITH CHECK.
  PERFORM set_config('app.current_user_id', actor_two::text, TRUE);
  BEGIN
    INSERT INTO shamaa_app.audit_log(actor_id,action,entity,entity_id,detail,created_at)
    VALUES(actor_one,'probe_spoof','user',actor_one::text,'must be rejected',1);
    RAISE EXCEPTION 'Mismatched audit actor unexpectedly passed RLS';
  EXCEPTION
    WHEN insufficient_privilege THEN NULL;
  END;

  -- INSERT permission does not imply read access: no SELECT policy exists.
  SELECT count(*) INTO visible_rows FROM shamaa_app.audit_log;
  IF visible_rows <> 0 THEN
    RAISE EXCEPTION 'Restricted actor unexpectedly read audit rows';
  END IF;
END
$$;

RESET ROLE;

DO $$
DECLARE
  actor_one BIGINT := NULLIF(current_setting('app.audit_actor_one', TRUE), '')::bigint;
  total_rows BIGINT;
BEGIN
  SELECT count(*) INTO total_rows FROM shamaa_app.audit_log
    WHERE action IN ('probe_append','probe_spoof');
  IF total_rows <> 1 OR NOT EXISTS (
    SELECT 1 FROM shamaa_app.audit_log
    WHERE actor_id=actor_one AND action='probe_append'
  ) THEN
    RAISE EXCEPTION 'Audit append contract did not preserve exactly the authorized event';
  END IF;
END
$$;

ROLLBACK;

REVOKE INSERT, SELECT ON shamaa_app.audit_log FROM shamaa_audit_probe;
REVOKE USAGE ON SEQUENCE shamaa_app.audit_log_id_seq FROM shamaa_audit_probe;
REVOKE USAGE ON SCHEMA shamaa_app FROM shamaa_audit_probe;
DROP ROLE shamaa_audit_probe;

SELECT 'SHAM''A actor-scoped audit append RLS contract: PASS' AS result;
