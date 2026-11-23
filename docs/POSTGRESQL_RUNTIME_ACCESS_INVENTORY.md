# PostgreSQL Runtime Access Inventory (Phase 2 / Batch 9)

Status: **preliminary static inventory; not an authorization specification and not a production-ready RLS design.**

Reviewed on 2026-11-23 against `agent/supabase-integration` at the then-current branch head. Sources reviewed: `server/application.py`, `server/security.py`, `server/postgres_database.py`, the route modules under `server/routes/`, `docs/ARCHITECTURE.md`, and `docs/SUPABASE_MIGRATION.md`.

## 1. Runtime wiring: blocking gap

- `create_app()` currently constructs `Database(s.db_path)` when no database is injected. `Settings.db_path` comes from `SHAMAA_DB`; the inspected application factory does not select `PostgresDatabase` from a PostgreSQL URL.
- `PostgresDatabase` validates an already-installed schema and deliberately does not create roles, grants, policies, or migrations.
- The adapter has request-scoped session-hash/user-id context and transaction-local `set_config(..., TRUE)`. `optional_user()` binds the verified session's user id when the adapter exposes `bind_request_user`.
- Fixed system-operation names are declared for auth bootstrap, rate limits, mail worker, and audit append. Static call-site review found `trusted_operation()` in auth registration/login/email verification/password reset and rate-limit handling. The mail worker **does** enter `mail.worker` around its outbox processing loop. `audit.append` has no call site: audit rows are inserted through the shared `audit()` helper in the caller's transaction, so the policy must preserve atomicity and derive the actor from the already-bound user or a tightly-scoped admin action rather than inventing an independent worker scope. Password-reset and token paths still need statement-level review before policy design.
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


## 8. Statement-level findings from the second pass

### Runtime selection and identity context

- `Settings` currently defines `SHAMAA_DB` as a filesystem path and has no PostgreSQL connection-string setting.
- `create_app()` unconditionally falls back to `Database(s.db_path)` when no database object is injected. It calls `initialize_catalog(db, s)` during startup. This must be reconciled with the PostgreSQL adapter's migration-owned reference data before wiring PostgreSQL into production.
- The HTTP middleware establishes `request_scope(SHA256(cookie))` for API/media requests. `optional_user()` validates the session against `sessions` and active `users`, then binds the returned user id. The adapter uses transaction-local `set_config(..., TRUE)` and resets ContextVars at scope exit.
- `system_operation()` deliberately clears request identity while active and rejects switching to a different nested operation. This is a useful isolation property, but tests must prove context restoration on exceptions, nested same-operation scopes, background tasks, and concurrent requests.
- The `for_user(user_id)` facade accepts an integer; never expose it as a way for handlers to choose a client-supplied identity. Prefer only the middleware/session-verified binding path in HTTP handlers.

### Confirmed system-operation call sites

| Operation | Observed use | Remaining policy requirement |
|---|---|---|
| auth.register | Registration transaction, user insert, initial token/session, audit | Permit only the minimum registration writes; prevent arbitrary role/active flags |
| auth.login | User lookup and session creation/re-read | Session-token-hash based lookup; no broad user-table reads |
| auth.verify_email | Token consumption and verified timestamp update | Match token purpose, expiry, unused state and same user |
| auth.forgot_password | Token creation and outbox enqueue | Avoid email enumeration; constrain token and outbox writes |
| auth.reset_password | Token consumption, password update, session revocation | Revoke only sessions for token owner; keep reset atomic |
| rate_limit.consume | Single rate-limit bucket upsert | Only digest bucket; no access to arbitrary buckets or other operational tables |
| mail.worker | `server/mail.py:deliver_pending()` wraps the outbox worker | Worker may claim/update only eligible outbox rows; do not grant access to users, orders, ledger or other business tables |
| audit.append | Declared in adapter allowlist; no call site found | Do not enable a generic bypass. Audit inserts currently occur inside business transactions through `security.audit()`; policy should use bound actor identity/admin authorization and retain transaction atomicity |

### First-pass route-to-table matrix

This matrix records direct SQL table use found in route modules. Indirect joins and helper calls still require review before policy SQL is authored.

| Route module | Direct/observed tables | High-risk boundary |
|---|---|---|
| auth | users, sessions, auth_tokens | anonymous bootstrap must be token/session constrained; password reset must never become arbitrary user update |
| catalog | categories, settings, products, stores, users, media, product_images, product_variants, seller_orders, order_items, notifications | public product/store projections must not expose owner/private columns; notifications always user-owned |
| seller | stores, users, products, categories, media, product_images, product_variants, inventory_movements, seller_orders, orders, ledger, coupons | every store-scoped action must derive store ownership from bound user; uploaded media must remain owner-bound |
| orders | carts, orders, seller_orders, order_items, order_events, products, product_variants, stores, coupons, coupon_redemptions, inventory_movements, ledger | buyer id comes only from session; seller transitions scoped through owned store; stock and order writes remain atomic |
| messages | conversations, messages, notifications, orders, seller_orders, stores, users | both conversation participants must be derived from the linked eligible shipment; message attachment/read paths cannot trust conversation id alone |
| reviews | reviews, review_reports, order_items, orders, seller_orders, products, stores, users | public review reads must exclude private report/moderation fields; create/report must prove purchase or reporter ownership |
| cases | order_cases, order_case_events, order_case_attachments, orders, seller_orders, stores, users, notifications | evidence download must re-check case party/admin permission and attachment-to-case linkage every time |
| returns | return_requests, return_request_events, order_events, order_items, orders, seller_orders, stores, users, notifications | ownership must follow purchased order item and shipment; admin queue/decision is a separate role path |
| admin | users, sessions, stores, products, orders, seller_orders, settings, categories, audit_log, ledger, mail_outbox, reviews, review_reports, media, order_items, notifications | global reads/writes must be admin-only; commission/settings/ledger are financially sensitive and audit must be atomic |

### Specific design hazards to resolve before policy SQL

1. **Startup catalog seeding:** `initialize_catalog()` is called unconditionally. The PostgreSQL production path must not seed or mutate reference data at web startup if migrations own catalog seeds.
2. **Admin vs recent-admin:** distinguish read-only admin routes from mutations requiring recent MFA/reauth; a single generic `is_admin` policy would over-authorize.
3. **Audit atomicity:** do not authorize all inserts into `audit_log` through a generic system flag. Preserve actor binding and same-transaction writes.
4. **Outbox worker isolation:** the mail worker should not inherit request identity and should receive only the outbox permissions it needs.
5. **Public catalog:** public listing endpoints select wide rows in some places. Before granting public table SELECT, replace with least-privilege views/projections or carefully scoped columns; do not expose user/session/private media fields.
6. **Financial separation:** seller ledger SELECT must be store-owner scoped; admin ledger access must be separate; no RLS policy should turn `cod_commission_due` into collected cash or withdrawable balance.
7. **Migration/owner split:** web runtime credentials must not be used by migration tooling; production schema changes remain a separately approved operation.

## 9. CI RLS evidence: distinguish test contracts from production policies

A focused review of migrations `0001` through `0013` and the PostgreSQL contract scripts confirms the following:

- The migrations enable **both** `ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY` across the 32 application tables. Later feature migrations repeat this for each newly introduced table. The migration set does not install application-facing row policies or grant the runtime role table access.
- `tests/postgres/rls_deny_all_contract.sql` creates a temporary `NOLOGIN NOSUPERUSER NOBYPASSRLS` probe role, grants it SELECT for the test, and verifies zero visible rows across all 32 tables. It also verifies that setting `app.current_user_id` or `app.system_operation` alone cannot bypass the deny-all baseline. The role and grants are removed afterward.
- `tests/postgres/session_hash_bootstrap_contract.sql` creates temporary policies inside a transaction and rolls them back. It tests cookie-digest-only session lookup, active-user visibility, expiry/revocation behavior, and resistance to a forged `app.current_user_id`. These are contract fixtures, not policies installed by the application migrations.
- `tests/postgres/audit_append_contract.sql` likewise installs a temporary actor-matched INSERT policy inside a transaction. It tests that an actor can append only an event attributed to that actor and cannot read audit rows through INSERT permission. The fixture is rolled back and its temporary role/grants are removed.

### Interpretation of the successful CI run

The successful PostgreSQL CI job proves that the deny-all baseline and the temporary bootstrap/audit policy contracts behave as asserted in the isolated CI database. It does **not** prove that the application has a complete production policy set, that the FastAPI runtime role has been provisioned, or that buyer/seller/admin routes work under a production-equivalent restricted role. In particular, the session and audit policies exercised by CI are deliberately not committed as production migrations.

### Next security gate

Before describing Supabase runtime access as implemented, add a reviewed, versioned policy-and-grant migration (or an explicitly documented equivalent provisioning procedure) for the dedicated runtime role. Then execute positive and negative route-level tests under that role for buyer A/B, seller A/B, public catalog projections, admin vs recent-admin, mail worker, and actor-attributed audit writes. Keep the deny-all tests as a separate fail-closed baseline. No live-project migration or credential change is part of this work.


## 10. Second-pass statement matrix: orders, messaging, cases, returns, administration

Reviewed the route implementations in server/routes/orders.py, messages.py, cases.py, returns.py, and admin.py. The matrix records SQL-visible scope and the application gate in the current code. It is not a substitute for database policies; helper queries and shared transitions are included.

| Route / handler | Reads | Writes | Application scope / policy implication |
|---|---|---|---|
| GET /cart, POST /coupon-quote | carts, products, product_variants, stores, coupons and quote inputs via commerce helpers | none | require_user; cart and coupon usage must be tied to the session-bound buyer. Public product eligibility is separate from private product rows. |
| PUT /cart | products/public product projection, active variants, buyer cart, cart line count | carts INSERT/UPDATE/DELETE | require_user; every cart mutation is constrained by user.id. Product/variant ids are client inputs but must resolve to an eligible public product and a variant belonging to it. |
| POST /orders checkout | buyer idempotency row, cart quote, product/commission data, stores/owners | orders, seller_orders, coupons, coupon_redemptions, products, product_variants, order_items, inventory_movements, order_events, carts, notifications, audit_log | verified_user; buyer id is session-derived. All writes are one transaction. Stock, coupon redemption, shipment creation and audit must remain atomic. Store owner is resolved from the store row, not request input. |
| GET /orders, GET /order-attempts/{key}, GET /orders/{id} | orders, seller_orders, order_items, order_events, stores | none | require_user; every root order lookup is filtered by buyer_id=user.id. Payload helpers then load child shipment/items/events by their parent ids; RLS must preserve the same parent-order buyer boundary. |
| Buyer shipment cancellation | orders, seller_orders, order_items | products/product_variants stock, inventory_movements, seller_orders, order_events, notifications, audit_log (the shared helper creates a commission-due ledger row only on delivery, not cancellation) | require_user; root order is buyer-filtered and shipment is constrained to that order. Only buyer-permitted early cancellation transitions are allowed. Shared change_status helper performs inventory restoration and records events/audit. |
| GET /seller/orders, PATCH /seller/orders/{id}/status | seller store, seller_orders, order_items, order_events, stores, parent order delivery fields | seller_orders, products/product_variants on cancellation, inventory_movements, order_events, ledger on delivery, notifications, audit_log | seller_user plus own_store; shipment SELECT must be limited to the store owned by the bound user. The seller response intentionally includes only fulfilment fields needed from the parent order. |
| GET /conversations, GET /conversations/{id}/messages | conversations joined to seller_orders, orders, buyer/seller users, stores; messages | none | require_user; each conversation must satisfy buyer-id OR store-owner-id membership. Message history is read only after the participant row is established. |
| POST /conversations | target seller_order, parent order buyer, store owner; participant conversation | conversations, messages, notifications, audit_log | verified_user; shipment id is client-supplied but membership is checked against the actual buyer and store owner before creation. Idempotency key is scoped by conversation and sender. |
| POST /conversations/{id}/messages | participant conversation and prior message by sender/client key | messages, conversations.updated_at, notifications, audit_log | verified_user; participant check precedes write. Notification text is fixed and does not copy user-authored message content. |
| GET /cases, GET /cases/{id} | order_cases, parent orders/shipments/stores/users, order_case_events, order_case_attachments | none | verified_user; list query filters buyer_id OR seller_id and detail helper independently checks party membership. |
| POST /cases | seller_order, order, store owner, eligible order_events | order_cases, order_case_events, notifications, audit_log | verified_user; only the buyer of an eligible shipped/delivered shipment can open a case. Buyer/seller ids are copied from joined records. |
| Case attachment upload/download | case party row, attachment metadata, per-case count/bytes/digest; storage file | order_cases.updated_at (quota serialization), order_case_attachments metadata and private filesystem object | verified_user for participant upload/download; upload locks/updates only an open case and enforces per-case quotas. Download must re-check participant membership and attachment-to-case linkage. Filesystem access is not governed by PostgreSQL RLS and needs separate private-path controls. |
| Case messages / events | case row and party membership | order_case_events, notifications, audit_log | verified_user; actor role is derived from buyer/seller identity. Admin attachment retrieval is a separate admin-gated path. |
| Admin case queue/detail/decision | all order_cases and related parent/order/user/event/attachment rows | order_case_events, order_cases, notifications, audit_log | Queue/detail use admin_user; decision uses recent_admin. A generic admin policy must not accidentally permit decision writes without recent reauthentication. |
| GET /returns, GET /returns/{id} | return_requests joined to order_items, seller_orders, orders, stores, buyer/seller users; return_request_events | none | verified_user; list is buyer_id OR seller_id scoped; detail helper checks party membership. |
| POST /returns | purchased order_item joined to shipment/order/store; delivery order_events; existing return quantities/statuses | return_requests, return_request_events, notifications, audit_log | verified_user; purchase lookup is buyer-filtered. Eligibility and quantity limits are derived from the purchased item and delivered shipment. |
| Seller return decision / receipt | participant return row and linked purchase/order data | return_request_events, return_requests, notifications, audit_log | verified_user; seller id must match the derived seller_id and the expected current state. |
| Buyer cancel / return shipment | participant return row and linked purchase/order data | return_request_events, return_requests, notifications, audit_log | verified_user; buyer id must match the derived buyer_id and the expected state. |
| Admin return queue/detail/decision | all return_requests and linked item/order/store/user/event rows | return_request_events, return_requests, notifications, audit_log | Queue/detail use admin_user; final decision uses recent_admin. Keep this distinct from buyer/seller participant policies. |
| Admin summary/users/stores/products/orders | global users, stores, products, orders, ledger, mail_outbox, sessions, categories, product_images, media, product_variants, seller_orders, order_items, order_events | user active/session revocation; store/product moderation and status; shared order status helper writes | Read paths use admin_user; moderation and account-state writes use admin_user; order status uses admin_user. These are global admin-only scopes, not owner-scoped seller access. |
| Admin settings/commission | settings, categories, stores | settings, stores/categories commission_bps, audit_log | Reads use admin_user; mutations use recent_admin. Commission table selection is allowlisted to stores/categories. Treat these as high-impact financial configuration writes. |
| Admin audit/ledger/reviews/reports | audit_log, ledger, reviews, review_reports, order_items, seller_orders, orders | reviews, review_reports, notifications, audit_log | Reads use admin_user; moderation/report resolution uses admin_user. Ledger is global admin visibility here; seller ledger remains a separate store-owner route and must not inherit admin-wide access. |

### Cross-cutting findings and unresolved concurrency checks

1. **Payload helpers expand the data surface.** order_dict() and shipment_dict() read shipment children, item snapshots, event history and store names. _conversation_select(), _case_row(), _case_payload(), _return_row(), and their payload helpers similarly join identities and child history. Policies must cover these indirect reads, not only the first SELECT in each handler.
2. **Participant checks are application joins, not database guarantees.** Messages, cases and returns establish party membership by joining the shipment/order/store owner. Equivalent row policies or carefully scoped database functions must enforce the same relationship even if a caller bypasses FastAPI.
3. **Case attachments cross the database/filesystem boundary.** PostgreSQL can protect metadata rows but cannot protect the local attachment directory. Keep storage private, use unguessable storage keys, and authorize every download against the current case party/admin relationship.
4. **Recent-admin is a distinct capability.** Settings, commissions, case decisions and return decisions require recent_admin; a role-only admin policy would collapse this distinction unless reauthentication freshness is represented and enforced in the database or those writes remain behind a trusted server boundary.
5. **Return state writes need a concurrency-focused review before RLS design is considered complete.** Several seller/buyer/admin return handlers append an event and then issue a conditional status UPDATE but do not check that UPDATE's rowcount. Under concurrent requests, a losing transition could otherwise continue to notification/audit code unless the transaction is explicitly aborted. Add tests for simultaneous seller decision vs buyer cancellation, duplicate return shipment/receipt, and admin decision vs participant action; require a successful conditional update before treating the transition as committed.
6. **Shared order status transitions also need race tests.** change_status() validates a previously read status and then updates by shipment id; review conditional compare-and-set semantics for concurrent seller/admin/buyer actions. Keep stock restoration, status event, ledger insert and audit in the same transaction.
7. **Admin read breadth is intentionally global but sensitive.** Summary and queues aggregate across users/stores; ledger, audit, buyer delivery details, private case/return details, and account email fields must remain inaccessible to ordinary users and sellers.
8. **No policies are inferred from these application gates.** This pass records what handlers do today. It does not establish that the database independently enforces the same predicates or that the current runtime can connect as a restricted PostgreSQL role.

### Next verification gate

- Add route-level isolation tests for buyer A vs buyer B and seller A vs seller B across orders, conversations, cases, attachments, and returns, including guessed ids and cross-store shipment ids.
- Add concurrent state-transition tests for return and shipment workflows; assert losing requests roll back event, notification and audit writes.
- Verify the PostgreSQL runtime role's SELECT/INSERT/UPDATE/DELETE privileges per table and the policies' USING/WITH CHECK behavior, including child-table joins.
- Only after those checks should the team design the versioned runtime role policy/grant migration. This remains a development-branch review; it does not authorize production credentials, live Supabase changes, deployment, or merging to main.
