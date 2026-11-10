# PostgreSQL runtime security design

Status: design only; this document does not grant privileges, create login roles, or install RLS policies in the live Supabase project.

## Security boundary

FastAPI remains the only application entry point. The `shamaa_app` schema is private and is not exposed through the Supabase Data API. Browser code must never receive a database connection string, database password, Supabase secret key, or a credential with `BYPASSRLS`.

The eventual database login must be a dedicated, non-owner role with `NOBYPASSRLS`. It must not be `postgres`, `service_role`, a table owner, or a member of a privileged Supabase role. It should receive only `CONNECT`, `USAGE` on `shamaa_app`, and the minimum table/sequence privileges needed by the application. Schema creation and migrations remain a separate administrative operation.

RLS is a second isolation layer for application query mistakes. Because the trusted FastAPI database role can set application-defined session variables, this design does not claim RLS protects against a fully compromised application server or stolen database credential. Preventing arbitrary SQL execution, keeping credentials server-side, and using parameterized SQL remain essential.

## Request identity lifecycle

The current application authenticates its own opaque cookie sessions; it does not yet use Supabase Auth. Preserve that model during the first database adapter rollout.

1. Hash the opaque session cookie in FastAPI exactly as today. Never pass the raw cookie to PostgreSQL or log either value.
2. Start a short, explicit database transaction for session lookup and set the transaction-local variable `app.session_token_hash` to that digest using parameter binding and `set_config(..., true)`.
3. The bootstrap RLS policy may expose only the session row whose `token_hash` equals that transaction-local digest. A narrowly-scoped matching policy may expose the associated user row for that same lookup. No general user enumeration is allowed.
4. After validating the session expiry and active user, FastAPI stores the verified numeric user ID in request-scoped server context. It must not accept an identity or role from request headers, query parameters, form fields, or browser storage.
5. Every later read/write transaction for that request sets `app.current_user_id` transaction-locally from the verified request context before application queries execute. Transaction-local settings must reset automatically at commit/rollback; never use session-level `SET` on pooled connections.
6. Registration, email verification, password reset, login throttling, and mail delivery are pre-authentication/system flows. They need explicit narrow flow contexts (for example, a matching one-time token digest or a server-only worker context), not a blanket unauthenticated policy.
7. A missing, malformed, or unset identity must deny private-row access. Admin checks must use the database user role re-read by the server, plus the existing recent-MFA requirement; never trust a client-supplied role.

The adapter must make it impossible for a pooled connection to retain one request's identity for the next request. Tests must cover commit, rollback, exceptions, concurrent requests, and connection reuse.

## Planned table access matrix

The matrix is a policy design target, not an installed policy set. All policies are scoped to the dedicated runtime role and default-deny remains in force.

| Table | Intended read scope | Intended write scope / special handling |
|---|---|---|
| `users` | Own row; narrowly matched session/token bootstrap; admin | Registration flow may insert; self-service fields only through server routes; admin changes server-authorized |
| `sessions` | Exact cookie-digest bootstrap; own sessions; admin | Login flow inserts; user may revoke own sessions; expiry cleanup is a server worker |
| `auth_tokens` | Exact one-time token digest during verify/reset; admin | Server auth flows issue, consume, and expire tokens |
| `rate_limits` | Server only; no browser-facing route | Server only; atomic upsert |
| `categories` | Public catalog reads | Admin/catalog migration writes only |
| `stores` | Active stores publicly; owner sees own store; admin sees all | Owner may request permitted edits; moderation/status changes admin-only |
| `media` | Public only when referenced by a public product; owner/admin for private media | Owner uploads/deletes own media through validated server flow |
| `products` | Active products in active stores publicly; owning seller and admin may inspect private statuses | Seller writes only to own store; approval/featured/status moderation admin-only |
| `carts` | Own cart | Own cart only |
| `orders` | Buyer owns order; admin | Buyer creates own order through server checkout; status/totals are server-controlled |
| `seller_orders` | Owning store seller; buyer through parent order; admin | Seller may perform allowed status transitions only; financial values server-controlled |
| `order_items` | Through authorized parent seller order / buyer order; admin | Checkout service only; immutable after order creation |
| `order_events` | Through authorized parent seller order; admin | Server inserts events; append-only trigger remains |
| `inventory_movements` | Owning seller / admin | Server inventory service only; append-only trigger remains |
| `ledger` | Owning seller / admin | Server finance service only; append-only trigger remains |
| `notifications` | Own notifications | Server creates; owner may mark own notification read |
| `audit_log` | Admin/security review only | Server inserts; append-only trigger remains |
| `settings` | Explicit public allowlist of storefront keys; admin otherwise | Admin/configuration service only |
| `mail_outbox` | Server mail worker only | Server enqueue/worker only; no user direct access |

## Required privilege controls

- Revoke schema, table, and sequence privileges from `PUBLIC`, `anon`, and `authenticated` for `shamaa_app`.
- Do not grant schema usage to browser-facing Supabase roles.
- Grant the runtime role only the exact table operations required by routes and workers; do not use blanket `GRANT ALL`.
- Grant sequence usage only for identity sequences actually used by inserts.
- Enable and FORCE RLS on every application table; policies must explicitly target the runtime role.
- Keep append-only triggers on `audit_log`, `ledger`, `order_events`, and `inventory_movements`.
- Keep migrations/admin credentials separate from runtime credentials. Rotate the runtime password if exposed.
- Do not enable policies in the live project until the same migration and negative tests pass in CI.

## Required CI security tests before deployment

1. A role with `NOBYPASSRLS` that is not table owner cannot read any private row with no identity context.
2. User A cannot read or mutate user B's cart, sessions, notifications, media, orders, or seller-owned records.
3. A seller cannot inspect another seller's private products, orders, inventory, or ledger.
4. Buyer access to seller-order details is possible only through the buyer's parent order.
5. Public catalog access returns only active products belonging to active stores.
6. Admin-only actions fail without a server-verified admin identity and recent MFA.
7. Session bootstrap exposes only the row matching the supplied hashed cookie; unknown/expired digests expose nothing.
8. Verification/reset bootstrap exposes only the matching unexpired, unused token and cannot enumerate users.
9. Transaction-local identity is cleared after commit and rollback and never leaks across pooled connection reuse.
10. Runtime role cannot create schemas/tables, alter policies, disable RLS, assume an owner role, or mutate append-only records.
11. Negative tests prove `anon` and `authenticated` cannot use the schema or access any table.
12. The same tests run against PostgreSQL 17 in CI before any live migration is applied.

## Rollout gates

- Gate A: finish and test the psycopg 3 adapter contract without changing the live database.
- Gate B: implement request-scoped identity propagation and transaction-local context; test connection reuse and concurrency.
- Gate C: implement the role/grant/RLS migration and the complete isolation test matrix in CI.
- Gate D: review the exact grants and policies, then deploy the migration to the dedicated Supabase project.
- Gate E: provision the login credential out-of-band, store it only in the server secret manager, and run a read-only smoke test.
- Gate F: migrate a sanitized SQLite copy, run parity and checkout/stock/ledger tests, then plan a controlled cutover with rollback.
  
Until all gates pass, SQLite remains the active application database and the live `shamaa_app` schema remains closed by default.


## Gate A — Psycopg adapter foundation

The first adapter implementation is isolated in `server/postgres_database.py`. It uses Psycopg 3 with autocommit connections and explicit transaction contexts, maps application qmark placeholders to Psycopg placeholders, returns the shared `HybridRow` contract, and translates integrity/operational exceptions into the application's existing database exception contract. It sets a validated, fixed-schema `search_path` at connection startup and disables automatic prepared statements for compatibility with transaction-pooling endpoints.

`PostgresDatabase.initialize()` is intentionally validation-only: migrations are applied out-of-band and the adapter refuses to start unless all 19 foundation tables exist. It does not create roles, grant privileges, open RLS, or change the live Supabase project. SQLite remains the default application backend; runtime switching is not enabled in this gate.

The PostgreSQL CI job now runs a real Psycopg integration test against its isolated PostgreSQL 17 service. The test covers schema validation, generated IDs via `RETURNING`, positional and named row access, qmark parameter binding, affected-row counts, commit, rollback, and integrity-error translation. It uses a disposable test table and removes it afterward.

## Gate A2 — FastAPI application smoke test on PostgreSQL

The PostgreSQL 17 CI job now boots the actual FastAPI application with the
Psycopg adapter injected through the existing database protocol. It verifies
health/schema checks, category bootstrap, public catalog reads, account
registration, session-cookie authentication, duplicate-email conflict
translation, and transactional email-outbox delivery using a private temporary
file transport. The test uses a unique synthetic email and isolated temporary
filesystem paths; it does not call external SMTP or the live Supabase project.

This CI database runs as its service superuser to apply the foundation
migration and inspect the test schema. A superuser bypasses RLS, so this smoke
test proves SQL/application compatibility only. It is NOT evidence that
RLS policies, runtime grants, or cross-user isolation work. Those remain Gate B
and Gate C requirements and must be tested with a dedicated non-owner
NOBYPASSRLS role before any production cutover.
