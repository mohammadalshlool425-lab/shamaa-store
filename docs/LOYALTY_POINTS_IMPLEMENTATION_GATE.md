# Batch 10 — Loyalty Points Implementation Gate

Status: **engineering preparation only**. This document is not authorization to issue, expire, reverse, redeem, or advertise points. No migration or runtime feature is introduced by this plan.

## 1. Repository facts checked before implementation

- SQLite's current schema contract reports version 12; its next migration must be version 13 and must update `LATEST_SCHEMA_VERSION` only when the migration and upgrade tests are committed together.
- The PostgreSQL/Supabase migration stream currently ends at `0013_seller_ledger_details.sql`; the corresponding next migration number is `0014`.
- The two migration streams must describe the same logical schema while respecting their dialects. Do not copy PostgreSQL-specific RLS/trigger syntax into SQLite.
- The PostgreSQL adapter is intentionally non-migrating and does not create roles, grants, or policies at application startup. A loyalty migration must not weaken that boundary.
- The current runtime-access inventory is preliminary; passing isolated PostgreSQL CI does not establish production Supabase runtime authorization.

## 2. Proposed immutable ledger contract

The first implementation slice, after policy approval, should add a private ledger table with the following logical fields:

| Field | Contract |
|---|---|
| `id` | Generated immutable primary key |
| `user_id` | Required FK to the buyer account; never accepted from buyer request bodies |
| `points_delta` | Signed bounded integer, non-zero |
| `entry_type` | Closed allow-list: grant, expiry, reversal; redemption only after separate approval |
| `reference_type`, `reference_id` | Required stable source reference |
| `idempotency_key` | Required server-generated or server-validated key |
| `expires_at` | Nullable only where the approved policy permits non-expiring entries |
| `created_at` | Server/database timestamp |

Required invariants:

- A grant is positive; expiry and reversal entries are negative. A redemption type must remain unavailable until approved.
- A unique idempotency constraint prevents retries from duplicating a grant or reversal.
- A second uniqueness rule must prevent a source event from being granted twice, scoped to the event type and reference. The exact key depends on the approved earning event.
- Foreign keys and checks must reject unknown users, zero deltas, unsupported types, and values outside the chosen integer range.
- UPDATE and DELETE are rejected by database triggers. Corrections are new compensating entries.
- Never store a mutable authoritative balance as the sole source of truth. Any cached balance must be transactionally maintained and independently reconcilable to the ledger.

## 3. Access-control design gate

- PostgreSQL table: ENABLE and FORCE ROW LEVEL SECURITY.
- No PUBLIC access, broad policies, or new runtime grants.
- Buyer read scope is derived only from the authenticated database request context; never trust a client-supplied `user_id`.
- Buyer write access to the ledger is denied. Grant/expiry/reversal writes are server-controlled and require a narrowly scoped, fixed operation context with a server-derived source event.
- The migration must fail closed if the required role/policy contract is absent. Do not add a policy that assumes the existing application connection is a Supabase Auth user.
- SQLite tests must separately prove owner scoping at the application layer; they do not substitute for PostgreSQL RLS tests.

## 4. Release sequencing

1. Obtain the owner decisions in `LOYALTY_POINTS_SPEC.md`.
2. Freeze the earning event and its authoritative proof, including COD cash-collection evidence.
3. Implement SQLite migration 13 and PostgreSQL migration 0014 in one reviewable change.
4. Add database-level append-only and bounds tests, migration-from-existing-data tests, and retry/concurrency tests.
5. Add PostgreSQL 17 tests for FORCE RLS, anonymous denial, user A/B isolation, spoofed user IDs, and server-only ledger writes.
6. Add read-only buyer API only after its database identity and RLS policy are proven. Responses must be private/no-store and paginated.
7. Add the AR/EN read-only UI only after the API exists; no redeem control or “available reward” claim before activation.
8. Run full CI. Production migration, merge, and deployment remain separate owner-controlled approvals.

## 5. Required test cases before acceptance

- Upgrade a populated SQLite v12 database to v13 without changing existing orders, coupons, commissions, or users.
- Apply PostgreSQL migration 0014 to a clean PostgreSQL 17 database after 0013.
- Verify ledger table has FORCE RLS and no unintended PUBLIC grants.
- Verify unauthenticated access and buyer A/B cross-account reads/writes fail.
- Verify buyer-supplied owner, delta, type, reference, expiry, or balance fields are ignored/rejected.
- Verify duplicate delivery/event retries and concurrent workers create at most one grant.
- Verify expiry boundary behavior, bounded reversals, and no mutation/deletion.
- Verify private API responses are no-store and absent from service-worker cache.
- Verify no change in checkout totals, coupon accounting, seller commission, COD collection, or return processing while loyalty is disabled.

## 6. Explicit stop conditions

Implementation must stop before issuing or redeeming points if any of the following is unresolved:

- earning rate and eligible amount basis;
- COD cash-collection evidence and authoritative event source;
- expiry duration and per-grant expiry semantics;
- redemption value, limits, and eligible catalogue scope;
- funding party and seller-commission treatment;
- returns after points were spent and whether a negative balance is permitted.

No default monetary conversion, promotional promise, or policy value is inferred by engineering.

## 7. Current disposition

**No code, schema, endpoint, UI, or feature flag has been added by this preparation step.** The safe next work is to complete the non-financial event-source and authorization review, then request only the policy decisions needed to cross the implementation gate. The existing project plan and production safety boundaries remain unchanged.


## 8. COD collection evidence review

See [COD_COLLECTION_EVIDENCE_GAP.md](COD_COLLECTION_EVIDENCE_GAP.md). The current workflow records shipment delivery and a commission-due ledger entry, but does not record an independent cash-collection confirmation. A delivered event or commission-due entry must never trigger a loyalty grant. The grant implementation gate remains closed until the owner approves the collection evidence, confirmer role, and correction process.
