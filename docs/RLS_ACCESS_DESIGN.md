# SHAM'A PostgreSQL RLS access design

> Design review only — 16 November 2026. This document is not an authorization migration and must not be treated as permission to enable the PostgreSQL runtime. No RLS policies, grants, roles, or production settings are changed by this document.

## 1. Verified database baseline

The branch contains ten ordered migrations under `supabase/migrations/`:

- `0001_shamaa_app_foundation.sql`: 19 core tables. A dynamic migration loop applies both `ENABLE ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY` to all 19.
- `0002_product_condition.sql`: condition fields on products and order snapshots.
- `0003_product_images.sql`: `product_images`.
- `0004_product_variants.sql`: `product_variants`.
- `0005_conversations_messages.sql`: `conversations`, `messages`.
- `0006_verified_reviews.sql`: `reviews`, `review_reports`.
- `0007_shipment_cases.sql`: `order_cases`, `order_case_events`.
- `0008_case_attachments.sql`: `order_case_attachments`.
- `0009_returns.sql`: `return_requests`, `return_request_events`.
- `0010_coupons.sql`: `coupons`, `coupon_redemptions`.

There are 32 application tables in total. The migration files explicitly apply ENABLE + FORCE RLS to the 13 tables added after the 19-table foundation. The migrations contain no `CREATE POLICY` statements and do not grant table access to PUBLIC. This is a deliberate deny-by-default baseline, not a completed RLS implementation.

The PostgreSQL adapter validates the pre-applied schema and does not create roles, grant privileges, apply migrations, or switch database roles. It currently accepts a generic PostgreSQL connection string. There is no reviewed dedicated runtime role or role-membership setup in the repository.

## 2. Identity contract already implemented

For requests under `/api/` and `/media/`, FastAPI middleware opens a request scope using only the SHA-256 digest of the session cookie. The server looks up a non-expired session joined to an active user. Only after that database lookup succeeds does `optional_user` bind the returned user id. The PostgreSQL adapter places `app.session_token_hash` and `app.current_user_id` in transaction-local settings with `set_config(..., TRUE)`, and resets the Python request context when the request ends.

The request-context test proves the verified-session lookup and subsequent user-id binding are visible inside PostgreSQL operations and do not persist into a later operation. It does **not** prove row isolation: current PostgreSQL tests run the schema contract with a privileged test connection, and no cross-user RLS policy tests exist yet.

A policy must fail closed when either setting is absent or malformed. Never derive the user id, role, or admin status from a request body, query parameter, cookie value other than the hashed session lookup, or arbitrary client header.

## 3. Proposed access classes

| Class | Tables | Intended access boundary |
|---|---|---|
| Identity | `users`, `sessions`, `auth_tokens` | A verified user may access only their own account/session records. Anonymous registration, login, verification and password-reset flows need narrowly scoped server-only operations. |
| Abuse controls | `rate_limits` | Server-side throttle operations only; never public or user-readable. |
| Public reference/catalog | `categories`, `stores`, `products`, `product_images`, `product_variants`, `media` | Anonymous reads only for active, published catalog records and images actually referenced by a published product. Store owners manage only their own store/catalog/media. Admin moderation is separate. |
| Buyer/seller commerce | `carts`, `orders`, `seller_orders`, `order_items`, `order_events`, `inventory_movements`, `ledger` | Buyer access follows order ownership; seller access follows store ownership and seller-order membership. Do not expose buyer address/contact fields to sellers through unrestricted parent-order access. Financial ledger rows are private to the owning seller and authorized admins. |
| User communication | `notifications`, `conversations`, `messages` | Notifications are owner-only. Conversations and messages are visible only to verified participants; inserts must bind sender to the current user and participant membership. |
| Trust and moderation | `reviews`, `review_reports` | Public reads only for approved reviews. Buyers can create/read their own verified-purchase reviews and reports; store owners receive only the intended review visibility; moderation is admin-only. |
| Disputes and returns | `order_cases`, `order_case_events`, `order_case_attachments`, `return_requests`, `return_request_events` | Buyer and seller participants only, with admin case-management access. Attachments remain private and must follow case membership, not public product-media rules. |
| Platform operations | `audit_log`, `settings`, `mail_outbox` | Audit rows are append-only and admin-readable; public bootstrap reads only the approved `home_*` settings subset; outbox reads/updates are worker-only. |
| Coupons | `coupons`, `coupon_redemptions` | Keep deny-all until coupon runtime routes and checkout redemption are implemented and tested. Future access is store-owner management, narrowly scoped buyer redemption, and admin audit. |

This matrix is a design target, not an assertion that each access path is already implemented.

## 4. Required trusted server contexts

The current request scope covers a verified cookie session, but it is not sufficient for all existing code paths. Before policies are written, the server needs explicit, narrowly named contexts for these operations:

1. **Anonymous authentication:** registration inserts a user, session, verification token, audit event and outbox message; login reads a user by normalized email and creates a session; verify/reset look up a one-time token and update only the linked account/session state; forgot-password must not reveal account existence.
2. **Rate limiting:** `security.throttle` reads, inserts, updates and prunes `rate_limits` for anonymous and authenticated requests.
3. **Mail worker:** `mail.deliver_pending` leases and updates `mail_outbox` rows outside a normal user request. This needs a worker-specific connection/context with only outbox privileges.
4. **Audit writes:** ordinary user actions append events attributed to the verified actor; system actions need an explicit system actor convention. Ordinary users must never read or mutate the audit log.
5. **Catalog initialization:** `create_app` calls `initialize_catalog` on startup, which upserts categories even when demo data is disabled. With deny-all RLS this startup write will fail. Move reference data seeding to reviewed migrations or an explicit one-time setup task; do not solve it with broad runtime write access.
6. **Demo seeding:** optional demo initialization writes users, stores and products. It must remain disabled in production and run only through a separately authorized setup path.

A broad `app.is_system=true` switch available to every route would erase the protection RLS is meant to provide. Prefer narrow operation identifiers and small server-side service functions, or a separate setup/worker role with restricted grants. Do not let arbitrary route handlers choose privileged operation labels.

## 5. Admin authorization without recursive RLS

Several admin routes need cross-user access to users, stores, products, orders, settings, audit, ledger, reviews and reports. A policy on `users` must not recursively query `users` to determine whether the current user is an admin.

Two acceptable designs need a focused security review before implementation:

- Bind a trusted admin assertion into transaction-local context only after the server has loaded the active user's role and verified the session. This requires changing the current binder to carry the database-verified role (and, where relevant, the session MFA timestamp); the value must never come from the browser.
- Use a narrowly scoped helper with a fixed safe `search_path`, a reviewed owner, minimal return value and tightly controlled EXECUTE privilege. Its owner and RLS behavior must be tested explicitly; do not casually grant BYPASSRLS or use a general-purpose SECURITY DEFINER helper.

The existing application has two admin assurance levels: `admin_user` requires admin role plus an MFA-authenticated session; `recent_admin` additionally requires reauthentication within ten minutes. RLS should enforce the base admin boundary while the route continues enforcing recent reauthentication for sensitive mutations.

## 6. Runtime role and connection requirements

Before any policy is enabled for application use:

- Use a dedicated database login for the FastAPI runtime, not the table owner, superuser, `service_role`, or any role with `BYPASSRLS`.
- The role must have only schema usage and the table/sequence/function privileges needed by the application. It must not own the schema or tables.
- Confirm how the chosen Supabase direct/pooler connection authenticates that role. Do not assume that a `NOLOGIN` role is usable without a reviewed membership/SET ROLE mechanism, and do not assume the current adapter changes roles—it does not.
- Keep the credential only in the server secret manager/environment. Never commit it or expose it to the browser/PWA.
- Keep migration ownership separate from runtime ownership. Migrations run through a controlled administrative path; the runtime cannot alter schema, policies, triggers or grants.

## 7. Test gates required before any RLS policy is accepted

Tests must run against PostgreSQL using the actual restricted runtime role with `NOBYPASSRLS`, not only the migration owner or a superuser.

- Create at least two buyers and two sellers with separate stores, carts, orders, conversations, reviews, cases, returns and financial rows.
- Prove user A cannot SELECT, INSERT as, UPDATE, DELETE or infer user B's private rows across every relevant table.
- Prove an anonymous connection cannot read or mutate private rows, and can read only the deliberately public catalog subset.
- Prove sellers cannot read another seller's inventory, ledger, orders, messages, media or private buyer address details.
- Prove buyers cannot read another buyer's orders, cart, notifications, cases, returns or messages.
- Prove an admin can perform only the documented admin operations and that a non-admin cannot obtain admin access by changing a request field or custom setting from the client.
- Prove expired, revoked, inactive-user and invalid-cookie sessions do not bind an identity.
- Prove transaction commit, rollback, exceptions, concurrent requests and connection reuse do not leak identity or operation context.
- Prove the mail worker and setup paths cannot read unrelated user/commerce data.
- Keep coupon tables deny-all until their application workflow is complete.

## 8. Current blockers and next implementation order

1. Finish a route-to-table operation inventory, including all SQL paths in auth, catalog, seller, orders, messages, reviews, cases, returns, admin, media and mail.
2. Define and implement trusted auth/system/worker contexts and move startup category seeding out of normal runtime startup.
3. Decide the Supabase runtime login and its exact grants/membership without using `service_role`.
4. Implement and test the non-recursive admin assertion.
5. Add RLS policies in small migration batches by access class, keeping all unreviewed tables deny-all.
6. Add restricted-role cross-user tests and negative tests for anonymous, seller, buyer and admin boundaries.
7. Only after all gates pass, consider a separate reviewed runtime-enablement and data-migration plan. Do not connect production or apply these policies to the live Supabase project as part of this design phase.


## 9. Route-to-table operation inventory (verified source review)

This inventory was checked against the route modules and shared services on the target branch. It records the current route boundary; it does not replace SQL-by-SQL policy tests. Tables not listed under a route remain deny-all until their consumers and workers are explicitly assigned.

| Area / route family | Current route guard | Tables and sensitive operations that policies must cover |
|---|---|---|
| Authentication: `/api/auth/*` | Mixed anonymous and authenticated | `users`, `sessions`, `auth_tokens`, `audit_log`, `mail_outbox`, `rate_limits`. Registration creates identity/session/token/outbox records; login reads by email and creates sessions; verification/reset consume one-time tokens; session listing/revocation is owner-scoped; MFA updates session state. |
| Public catalog: `/api/catalog/bootstrap`, `/products`, `/products/{id}`, `/stores` | Public reads; store detail currently requires `require_user` | `categories`, `settings`, `products`, `stores`, `users` (active seller check), `product_images`, `product_variants`, `media`. Anonymous visibility must be restricted to active/published rows and approved bootstrap settings. |
| Notifications: `/api/notifications*` | `require_user` | `notifications`; list/update must match `user_id = app.current_user_id`. Notification email enqueue also touches `users` and `mail_outbox` through shared service code. |
| Seller store/catalog: `/api/seller/store*`, `/products*`, `/inventory*`, `/media` | `verified_user` or `seller_user` | `users`, `stores`, `products`, `categories`, `media`, `product_images`, `product_variants`, `inventory_movements`. Store/product/media ownership must be derived through the verified owner/store relationship; client-supplied ids alone are not authority. |
| Buyer cart/checkout/orders: `/api/cart*`, `/api/orders*` | `require_user` or `verified_user` | `carts`, `orders`, `seller_orders`, `order_items`, `order_events`, `products`, `product_variants`, `stores`, `inventory_movements`, `ledger`. Buyer reads follow `orders.buyer_id`; seller suborders follow store ownership. Seller access must not expose unrelated buyer address/contact columns on the parent order. |
| Seller order management: `/api/seller/orders*` | `seller_user` | `seller_orders`, `stores`, `orders`, `order_items`, `order_events`, `inventory_movements`, `ledger`. Mutations must verify that the seller owns the specific `seller_orders.store_id`; transitions must remain constrained by application state rules. |
| Conversations/messages: `/api/conversations*` | `require_user` for reads; `verified_user` for writes | `conversations`, `messages`, and related `seller_orders` when a conversation is order-linked. Both conversation participants are the access boundary; message sender must equal the verified current user. |
| Reviews/reports: `/api/reviews*` | Public approved-review reads; authenticated report; verified review creation | `reviews`, `review_reports`, `orders`, `order_items`, `seller_orders`, `stores`, `products`. Public reads are approved-only; creation must prove an eligible completed purchase; reports are reporter-owned; moderation is admin-only. |
| Cases/attachments: `/api/cases*` | `verified_user`; admin routes use `admin_user` / `recent_admin` | `order_cases`, `order_case_events`, `order_case_attachments`, `orders`, `seller_orders`, `stores`, `users`. Buyer and seller participant checks apply to every list, detail, event and attachment access; admin decisions retain recent-MFA route checks. |
| Returns: `/api/returns*` | `verified_user`; admin routes use `admin_user` / `recent_admin` | `return_requests`, `return_request_events`, `orders`, `seller_orders`, `stores`, `users`. Buyer and relevant seller are the only normal participants; admin decisions remain separately guarded. |
| Admin: `/api/admin/*` | `admin_user` for reads/ordinary moderation; `recent_admin` for sensitive changes | Cross-table reads/writes include `users`, `sessions`, `stores`, `products`, `orders`, `seller_orders`, `settings`, `categories`, `audit_log`, `ledger`, `reviews`, `review_reports`, `mail_outbox`. RLS admin assertion must be trusted and non-recursive; route-level recent reauthentication remains required. |
| Media delivery: `/media/{media_id}` | Request middleware establishes optional verified identity; actual media row lookup is in application layer | `media`, and for public product images the related `product_images` / `products` / `stores` publication chain. Private media (especially case attachments) must not inherit public catalog visibility. |
| Startup and workers | Not a user route | `categories`, `settings`, `mail_outbox`, and optional demo `users` / `stores` / `products`. Startup catalog upsert must be removed from normal runtime or replaced by a separately reviewed migration/setup action. The mail worker needs an outbox-only role/context and must not receive broad application-table access. |

### Identity bootstrap dependency that must be solved first

The current verified-session lookup joins `sessions` to `users` before `app.current_user_id` can be set. Therefore the first authenticated database operation has only `app.session_token_hash`. The eventual policies must deliberately support this narrow bootstrap:

- Permit a session row lookup only when its `token_hash` equals the transaction-local `app.session_token_hash` and the session is not expired/revoked.
- Permit the joined user row only when a matching session for that same user and hash exists and the user is active.
- Do not grant general anonymous reads on `sessions` or `users`.
- Login-by-email, registration, verification and password-reset cannot use the verified-user context; they require separate server-only operations with narrowly scoped SQL/functions or a dedicated auth connection role. A client must not be able to select the operation name or supply an arbitrary user id.
- Rate limiting is invoked before several authentication routes and therefore needs an anonymous-safe, isolated path that exposes only the specific bucket operation, not general `rate_limits` table reads.

The media route also needs a split policy: published product media may be public, but unassociated uploads and case evidence must remain private and owner/participant-scoped. A single `media.status` public policy is not sufficient unless the association and parent publication checks are enforced.

### Remaining evidence required before policy migrations

1. Add focused tests for the session-hash-only bootstrap query, including invalid, expired, revoked and inactive-user sessions under the restricted role.
2. Exercise each auth operation through its proposed context and prove that it cannot read arbitrary users, tokens, sessions or outbox rows.
3. Prove mail worker privileges are limited to leasing/updating outbox rows and cannot read users, orders, ledger or messages.
4. Replace startup category writes with migration-owned seed data or a controlled one-time setup step, then test normal application startup under the restricted runtime role.
5. Test public catalog and public media association chains separately from private uploads and case attachments.


## 10. Proposed context model — implementation contract (reviewed, not yet coded)

The following is the smallest useful context vocabulary identified from the current call graph. These labels are internal server constants, not request parameters. A route must call a specific service function; it must not accept an operation label from JSON, query parameters, headers or cookies.

| Context | Allowed purpose | Explicitly excluded |
|---|---|---|
| `auth.register` | Insert one new user and the associated initial session, verification token, audit event and verification-mail outbox item as one bounded operation. | Listing users, reading arbitrary existing users, changing roles, reading existing sessions/tokens. |
| `auth.login` | Look up one normalized email for password verification; after successful verification create a session and update only the intended login metadata. | User listing, password-hash export, arbitrary account mutation, token enumeration. |
| `auth.verify_email` | Consume one matching unexpired verification token and update only its subject account. | Enumerating tokens/users or consuming a token for a different subject. |
| `auth.forgot_password` | Create a reset token for a matching account without changing the externally observable response for unknown email addresses. | Reading arbitrary account details or returning whether an email exists. |
| `auth.reset_password` | Consume one matching unexpired reset token and update that subject's password/session revocation state. | Arbitrary password or role updates. |
| `rate_limit.consume` | Atomically prune expired buckets and read/increment/upsert only the single server-computed bucket key. | Listing buckets, reading other bucket values, caller-selected SQL/table access. |
| `mail.worker` | Lease eligible outbox rows, update delivery status/attempt metadata and finish the lease. | Reading users, sessions, orders, ledger, messages or arbitrary outbox history. |
| `catalog.setup` | No normal-runtime context should be needed after category/reference rows are moved into versioned migrations. If a one-time setup command remains, it must use a separate deployment credential and not the web runtime. | Startup writes under the normal runtime login; demo seeding in production. |
| `audit.append` | Prefer actor-bound append from the verified user context. A system actor may be used only by a fixed internal worker/service path. | General audit reads, update/delete, arbitrary actor attribution from client input. |

### Context construction and transaction rules

- Keep the existing per-database-instance `ContextVar` isolation and transaction-local PostgreSQL settings. Add a distinct internal operation field only if it is set by a fixed server-side context manager with a closed allowlist.
- The context manager must restore its previous value in `finally`, including exceptions and nested calls. No context may survive request completion, rollback or pooled-connection reuse.
- Bind the authenticated user only after the session lookup has succeeded. The identity bootstrap query must be independently constrained by the cookie digest and session validity; it cannot rely on an already-bound user id.
- Authentication contexts should be implemented as narrowly scoped repository/service methods, not as a general-purpose `run_as_system(operation, callback)` API exposed throughout route code.
- Prefer SQL statements that constrain the affected subject using the token row itself (for example, a token-consumption CTE returning its subject id) rather than granting a context broad table-wide SELECT or UPDATE rights.
- Rate-limit bucket keys must be derived on the server from route scope and a normalized IP/account identifier, then hashed. The client must never supply the bucket key.
- Mail delivery should use a separate worker connection/login when the hosting topology supports it. If transaction-local operation context is retained instead, the database policy must still limit the worker to the outbox operations and the context must be unforgeable by normal request code.
- The runtime login must be a non-owner, `NOBYPASSRLS` role. Context labels do not replace SQL grants: table grants and RLS policies must both enforce least privilege.
- Do not implement these contexts by granting `service_role`, `BYPASSRLS`, schema ownership, or unrestricted `SECURITY DEFINER` functions.

### Startup catalog correction

Current `initialize_catalog(db, settings)` always inserts reference categories before checking whether demo mode is enabled. The safe target is to move the nine stable category records into a versioned migration using idempotent inserts. Normal application startup should then perform no database writes for category initialization. Demo users/stores/products remain an explicit development-only fixture path and must not run on production startup. A startup test under the restricted runtime role must prove this.

### Implementation sequence and acceptance evidence

1. Add unit tests for the closed operation allowlist, rejection of unknown/client-supplied labels, context restoration after exceptions, nested-context behavior and isolation across database instances.
2. Add a migration for the stable category reference data; remove the unconditional startup upsert and add a restricted-role startup test.
3. Implement auth operations one at a time with tests proving each operation can affect only its token/account subject.
4. Implement the rate-limit repository operation and test that the caller can observe only its own bucket outcome.
5. Split mail delivery onto the worker credential/context and test that it cannot query any non-outbox table.
6. Only then create the runtime role/grants and begin small RLS policy migrations with cross-user negative tests.

No role, grant, policy, production connection or runtime setting is changed by this design section. The current deny-all RLS posture remains intentional until the restricted-role tests pass.


## 11. Startup category seeding moved to migration (implemented)

The stable nine reference categories are now installed by `supabase/migrations/0011_seed_reference_categories.sql` using idempotent inserts. The PostgreSQL adapter declares that its catalog is migration-managed; `initialize_catalog` therefore performs no category writes for PostgreSQL startup. Demo seeding through the PostgreSQL web runtime is rejected and remains a separate development setup concern. SQLite development/test bootstrap behavior is unchanged.

The PostgreSQL CI workflow applies migration 0011 before the schema contract. The contract checks the nine expected categories and the electronics/audio parent relationship. The adapter integration test checks that migration-managed categories exist before application startup. This proves migration ownership and removes the unconditional category upsert from PostgreSQL runtime startup; it does not yet prove operation under a restricted NOBYPASSRLS runtime login, which remains a required gate.
