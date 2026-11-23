# SHAM'A Loyalty Points — Batch 10 Technical Specification

Status: design baseline only; no points are issued, redeemed, or treated as money.

## Goals

- Add an auditable, append-only promotional-points ledger for buyer accounts.
- Make earned, expired, reversed, and (only after a separately approved policy) redeemed points traceable to immutable references.
- Prevent duplicate grants, cross-account access, concurrent double-spend, and use of expired points.
- Keep Arabic/English buyer views consistent with the server-calculated balance.
- Preserve existing order totals, seller commissions, coupons, returns, and COD accounting until a points policy is explicitly approved.

## Non-goals until owner approval

- No JOD conversion rate, cash value, cash-out, transfer between users, or checkout redemption.
- No automatic grant merely because a COD shipment status changed to delivered. Delivered is not proof that cash was collected.
- No change to seller commission basis or coupon stacking.
- No automatic financial refund or ledger mutation when a return/case is opened or closed.

## Data model proposal

Use an immutable `loyalty_ledger` with:
- `id`, `user_id`, signed integer `points_delta`, `entry_type`,
  `reference_type`, `reference_id`, `idempotency_key`,
  `expires_at`, and `created_at`.
- Positive entries for grants; negative entries only for explicit reversals or future approved redemption.
- Unique idempotency key and unique reference/type constraints appropriate to the event.
- Database checks for non-zero integer deltas, supported entry types, valid expiry ordering, and bounded integer values.
- UPDATE/DELETE rejection at database level. Corrections are compensating entries, never edits.
- PostgreSQL ENABLE + FORCE RLS, with no PUBLIC grants or broad policies added by this feature.

Balance is derived from unexpired ledger entries (or a transactionally maintained cache that is always reconcilable to the ledger); the ledger remains authoritative. Any future redemption must reserve/consume points atomically under a row lock or equivalent serializable transaction and must re-check expiry at commit time.

## Grant eligibility and reversals

- The earning event must be defined by a future approved policy and a verified business event, not by a client request.
- A grant must reference the immutable order item/shipment snapshot and be idempotent under retries and concurrent delivery events.
- Cancellation before eligibility produces no grant.
- Approved return/refund events require a compensating entry limited to the unexpired/unspent grant amount; a spent-points debt policy must be explicitly decided rather than silently creating a negative balance.
- Expiration is calculated from each grant's own `expires_at`; expiry processing is idempotent and cannot make already expired points spendable.

## API and UI boundary

- Buyer-only read endpoints return the authenticated user's available balance and paginated ledger history.
- A buyer cannot submit `user_id`, delta, entry type, expiry, or balance values.
- Seller/admin endpoints are not part of the initial grant/read scope.
- AR/EN UI may show the points feature as unavailable until policy activation; it must not imply a usable reward balance before grants are enabled.
- Private endpoints use no-store and must not enter the service-worker cache.

## Test acceptance

- SQLite migration from current schema version to next version preserves existing data.
- PostgreSQL 17 migration applies cleanly and keeps the new table fail-closed under FORCE RLS.
- A/B user isolation, invalid owner spoofing, duplicate event/retry, concurrent grant, negative/overflow values, expiry boundary, reversal bounds, append-only enforcement, and pagination.
- If redemption is later approved: concurrent redemption, double-spend, expired-at-commit, and order/coupon/commission reconciliation.
- Chromium AR/EN and mobile tests verify private data handling and no offline cache leakage.

## Owner decisions required before enabling grants or redemption

1. Points earned per JOD (or per eligible order amount), and whether shipping/tax/discounts are excluded.
2. Whether COD earns only after a separate cash-collection confirmation.
3. Expiration duration and whether expiry is rolling per grant.
4. Redemption value, minimum/maximum redemption, and eligible products/categories.
5. Whether the discount is merchant-funded, platform-funded, or split; how seller commissions are calculated after redemption.
6. Treatment of returns after points were redeemed and whether a negative points balance is ever permitted.

Until these decisions are approved, implementation must remain disabled-by-default and must not create customer-facing financial promises.
