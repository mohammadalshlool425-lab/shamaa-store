# PostgreSQL Runtime Access Inventory (Phase 2 / Batch 9)

Status: **preliminary static inventory; not an authorization specification and not a production-ready RLS design.**

Reviewed on 2026-11-23 against `agent/supabase-integration` at the then-current branch head. Sources reviewed: `server/application.py`, `server/security.py`, `server/postgres_database.py`, the route modules under `server/routes/`, `docs/ARCHITECTURE.md`, and `docs/SUPABASE_MIGRATION.md`.

## 1. Runtime wiring: blocking gap

- `create_app()` currently constructs `Database(s.db_path)` when no database is injected. `Settings.db_path` comes from `SHAMAA_DB`; the inspected application factory does not select `PostgresDatabase` from a PostgreSQL URL.
- `PostgresDatabase` validates an already-installed schema and deliberately does not create roles, grants, policies, or migrations.
- The adapter has request-scoped session-hash/user-id context and transaction-local `set_config(..., TRUE)`. `optional_user()` binds the verified session's user id when the adapter exposes `bind_request_user`.
- Fixed system-operation names are declared for auth bootstrap, rate limits, mail worker, and audit append. `trusted_operation()` is currently used by the rate-limit path; every other operation needs a call-site audit before any corresponding policy is written.
- Therefore the FastAPI/PostgreSQL path is **not yet a complete runtime integration**. Passing CI's adapter and smoke tests is not evidence that production FastAPI is connected to Supabase.

No SQL policies, role grants, runtime connection settings, or production migrations are authorized by this inventory.

## 2. Route groups and application authorization gates

All paths below are API routes after their router prefixes are applied. The dependency gate is an application-layer check; RLS must independently enforce row scope.

| Module | Routes / operations | Application gate observed |
|---|---|---|
| auth | register, login, me, logout, verify, resend-verification, forgot/reset-password, list/revoke sessions, reauth | optional/required user depending on route; bootstrap operations require separate system scope |
| catalog | bootstrap, products list/detail, stores list/detail, notifications/read | public catalog reads; notifications require authenticated owner |
| seller | store CRUD, products CRUD, image/variant management, stock, inventory, summary, media upload, ledger, coupons | verified seller; enforce store ownership for every write/read |
| orders | cart, coupon quote, cart update, create/list/read order, order-attempt lookup, cancel shipment, seller order list/status | buyer-owned cart/orders; seller-owned shipments; verified user/seller gates |
| messages | conversations list/create, message list/create | verified participant; buyer/seller membership must be derived from order/shipment, never client-supplied |
| reviews | eligible list, create, public product reviews, report | verified buyer for write; eligible purchased item; reports tied to reporter |
| cases | buyer/seller case list/detail/create, private attachment upload/download, messages; admin queue/decision | verified party or admin; attachment access must be rechecked on every download |
| returns | buyer/seller list/detail/create, seller decision, cancel, return shipment, received; admin queue/decision | verified party or recent admin; order-item/shipment ownership and state guards |
| admin | summary, users, stores/review, products/review, orders/status, settings, commissions, audit, ledger, reviews/moderation/reports | admin; sensitive changes additionally require recent reauthentication |

The HTML page routes are not data authorization boundaries. API handlers, database constraints, and RLS are the relevant controls.

## 3. PostgreSQL application tables in the adapter contract

The adapter currently requires these 32 tables:

`users`, `sessions`, `auth_tokens`, `rate_limits`, `categories`, `stores`, `media`, `products`, `carts`, `orders`, `seller_orders`, `order_items`, `order_events`, `inventory_movements`, `ledger`, `notifications`, `audit_log`, `settings`, `mail_outbox`, `product_images`, `product_variants`, `conversations`, `messages`, `reviews`, `review_reports`, `order_cases`, `order_case_events`, `order_case_attachments`, `return_requests`, `return_request_events`, `coupons`, `coupon_redemptions`.

## 4. Preliminary data-scope classification

This is a policy-design starting point only. It does not claim the current migrations contain these policies.

| Data group | Tables | Candidate access boundary to prove |
|---|---|---|
| Identity and sessions | users, sessions, auth_tokens | user reads own profile/session; raw auth tokens remain server-only; registration/login/verification/reset use narrowly scoped system operations |
| Public reference/catalog | categories, active products/stores, public product images | public SELECT only for active/approved catalog projection; seller owns private drafts and media; no public access to private media |
| Seller workspace | stores, products, media, product_images, product_variants, inventory_movements | store owner may access only their store's rows; ownership derived by joins to stores/users; admin moderation is separate |
| Buyer commerce | carts, orders, order_items, notifications | buyer owns cart/order/notification; order-item access inherited from parent order and buyer; no client-selected buyer id |
| Seller fulfillment | seller_orders, order_events | seller sees only shipments for stores they own and minimum buyer delivery data required; buyer sees only their parent order; system transitions guarded |
| Financial records | ledger, settings, coupon definitions/redemptions | seller reads only own store ledger; admin-only global rates/settings; coupon reads/writes scoped to owning store and redemption actor/order; immutable financial history |
| Communications | conversations, messages | only verified buyer/seller participants attached to the eligible shipment; no access by guessed conversation id |
| Trust and disputes | reviews, review_reports, order_cases, order_case_events, order_case_attachments, return_requests, return_request_events | buyer/seller parties see only their own case/return and permitted evidence; public review projection excludes private moderation/report fields; admin decisions separately guarded |
| Operations | rate_limits, mail_outbox, audit_log | server-only narrow operation contexts; append-only audit writer; no user-facing table access |

## 5. System operations requiring explicit design

The adapter declares the following fixed operation names:

- `auth.register`
- `auth.login`
- `auth.verify_email`
- `auth.forgot_password`
- `auth.reset_password`
- `rate_limit.consume`
- `mail.worker`
- `audit.append`

Before policy implementation, trace each operation to every actual call site and SQL statement. Confirm that operation names cannot be supplied by a request, that the database role cannot choose arbitrary operation values, and that system contexts cannot nest or leak into user requests. Rate-limit bucket values must remain server-derived digests. Mail and audit operations need separate minimal privileges and append-only rules.

## 6. Required next implementation sequence

1. Complete a statement-level route-to-table matrix, including indirect joins and all write statements; identify which statements need both user identity and a system operation.
2. Decide the supported runtime architecture: inject `PostgresDatabase` through explicit configuration, keep SQLite development/test support, and fail closed when production is configured for PostgreSQL but the URL/role/schema is missing.
3. Create and verify a dedicated runtime role that is not a table owner, superuser, or `BYPASSRLS`; separate migration/owner credentials from runtime credentials. Do not grant broad schema/table privileges as a shortcut.
4. Design policies table-by-table from the ownership model above, including SELECT visibility required by UPDATE, and both `USING` and `WITH CHECK` for updates. Keep private schemas and Data API exposure closed unless explicitly required.
5. Test buyer A vs buyer B, seller A vs seller B, cross-store IDs, admin and recent-admin paths, anonymous requests, expired sessions, system operations, concurrent requests, transaction rollback, and connection reuse under the actual restricted role.
6. Run Supabase security advisors and the complete CI suite. Only after review should a migration be proposed; do not apply it to the live Supabase project as part of this development task.

## 7. Explicit non-claims

- This inventory does not certify complete route coverage or prove row isolation.
- CI's temporary PostgreSQL roles and policies are test fixtures, not production role configuration.
- No RLS policies or runtime grants are added by this document.
- No connection to the user's live Supabase project, migration, data movement, deployment, or `main` merge was performed.
