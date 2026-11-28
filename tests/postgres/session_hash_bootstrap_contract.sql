-- Test-only transaction proving the first authenticated lookup can be
-- constrained by the cookie digest alone. Policies and fixture rows roll back.
-- This is deliberately NOT an application migration.
\set ON_ERROR_STOP on

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'shamaa_session_probe') THEN
    CREATE ROLE shamaa_session_probe NOLOGIN NOSUPERUSER NOBYPASSRLS;
  END IF;
END
$$;
ALTER ROLE shamaa_session_probe NOLOGIN NOSUPERUSER NOBYPASSRLS;
GRANT USAGE ON SCHEMA shamaa_app TO shamaa_session_probe;
GRANT SELECT ON shamaa_app.users, shamaa_app.sessions TO shamaa_session_probe;

BEGIN;

-- Seed fixtures before enabling the temporary policies. CI connects as the
-- isolated database superuser; the probe role below receives SELECT only.
INSERT INTO shamaa_app.users(name,email,password_hash,created_at,active)
VALUES
  ('Bootstrap active','bootstrap-active@example.invalid','not-a-password',1,TRUE),
  ('Bootstrap inactive','bootstrap-inactive@example.invalid','not-a-password',1,FALSE);

INSERT INTO shamaa_app.sessions(token_hash,user_id,csrf_token,created_at,expires_at,ip_hash,user_agent)
SELECT 'a' || repeat('a',63), id, 'csrf-active', 1,
       extract(epoch FROM clock_timestamp())::bigint + 3600, 'ip', 'CI'
FROM shamaa_app.users WHERE email='bootstrap-active@example.invalid';

INSERT INTO shamaa_app.sessions(token_hash,user_id,csrf_token,created_at,expires_at,ip_hash,user_agent)
SELECT 'b' || repeat('b',63), id, 'csrf-expired', 1,
       extract(epoch FROM clock_timestamp())::bigint - 3600, 'ip', 'CI'
FROM shamaa_app.users WHERE email='bootstrap-active@example.invalid';

INSERT INTO shamaa_app.sessions(token_hash,user_id,csrf_token,created_at,expires_at,ip_hash,user_agent)
SELECT 'c' || repeat('c',63), id, 'csrf-inactive', 1,
       extract(epoch FROM clock_timestamp())::bigint + 3600, 'ip', 'CI'
FROM shamaa_app.users WHERE email='bootstrap-inactive@example.invalid';

CREATE POLICY session_hash_bootstrap_select ON shamaa_app.sessions
  FOR SELECT TO shamaa_session_probe
  USING (
    token_hash = NULLIF(current_setting('app.session_token_hash', TRUE), '')
    AND expires_at > extract(epoch FROM statement_timestamp())::bigint
  );

-- This is intentionally non-recursive: users checks sessions; the sessions
-- policy above does not query users.
CREATE POLICY active_user_session_bootstrap_select ON shamaa_app.users
  FOR SELECT TO shamaa_session_probe
  USING (
    active
    AND EXISTS (
      SELECT 1 FROM shamaa_app.sessions s
      WHERE s.user_id = users.id
        AND s.token_hash = NULLIF(current_setting('app.session_token_hash', TRUE), '')
        AND s.expires_at > extract(epoch FROM statement_timestamp())::bigint
    )
  );

SET ROLE shamaa_session_probe;

DO $$
DECLARE
  active_user_id BIGINT;
  inactive_user_id BIGINT;
BEGIN
  IF current_user <> 'shamaa_session_probe' THEN
    RAISE EXCEPTION 'Session bootstrap probe did not assume restricted role';
  END IF;

  -- Valid cookie digest: exactly its unexpired session and active user appear.
  PERFORM set_config('app.session_token_hash', 'a' || repeat('a',63), TRUE);
  SELECT user_id INTO active_user_id FROM shamaa_app.sessions;
  IF (SELECT count(*) FROM shamaa_app.sessions) <> 1
     OR (SELECT count(*) FROM shamaa_app.users) <> 1
     OR NOT EXISTS (SELECT 1 FROM shamaa_app.users WHERE id=active_user_id) THEN
    RAISE EXCEPTION 'Valid session hash did not reveal exactly its active user';
  END IF;

  -- Read the inactive account id through its valid session, then verify a
  -- forged current_user_id cannot widen the active-session user result.
  PERFORM set_config('app.session_token_hash', 'c' || repeat('c',63), TRUE);
  SELECT user_id INTO inactive_user_id FROM shamaa_app.sessions;
  PERFORM set_config('app.session_token_hash', 'a' || repeat('a',63), TRUE);
  PERFORM set_config('app.current_user_id', inactive_user_id::text, TRUE);
  IF (SELECT count(*) FROM shamaa_app.users) <> 1
     OR EXISTS (SELECT 1 FROM shamaa_app.users WHERE id=inactive_user_id) THEN
    RAISE EXCEPTION 'Forged user id widened session bootstrap visibility';
  END IF;

  -- Expired session: neither session nor user is visible.
  PERFORM set_config('app.session_token_hash', 'b' || repeat('b',63), TRUE);
  IF (SELECT count(*) FROM shamaa_app.sessions) <> 0
     OR (SELECT count(*) FROM shamaa_app.users) <> 0 THEN
    RAISE EXCEPTION 'Expired session exposed a session or user row';
  END IF;

  -- Valid session for an inactive account: session lookup succeeds, user JOIN
  -- returns no account, matching optional_user's bootstrap behavior.
  PERFORM set_config('app.session_token_hash', 'c' || repeat('c',63), TRUE);
  IF (SELECT count(*) FROM shamaa_app.sessions) <> 1
     OR (SELECT count(*) FROM shamaa_app.users) <> 0 THEN
    RAISE EXCEPTION 'Inactive account was exposed through a valid session';
  END IF;

  -- Revoked/unknown and malformed digests have no matching session.
  PERFORM set_config('app.session_token_hash', 'd' || repeat('d',63), TRUE);
  IF (SELECT count(*) FROM shamaa_app.sessions) <> 0
     OR (SELECT count(*) FROM shamaa_app.users) <> 0 THEN
    RAISE EXCEPTION 'Revoked or unknown session hash exposed rows';
  END IF;

  PERFORM set_config('app.session_token_hash', 'not-a-sha256-digest', TRUE);
  IF (SELECT count(*) FROM shamaa_app.sessions) <> 0
     OR (SELECT count(*) FROM shamaa_app.users) <> 0 THEN
    RAISE EXCEPTION 'Malformed session hash exposed rows';
  END IF;
END
$$;

RESET ROLE;
ROLLBACK;

REVOKE SELECT ON shamaa_app.users, shamaa_app.sessions FROM shamaa_session_probe;
REVOKE USAGE ON SCHEMA shamaa_app FROM shamaa_session_probe;
DROP ROLE shamaa_session_probe;

SELECT 'SHAM''A session-hash bootstrap RLS contract: PASS' AS result;
