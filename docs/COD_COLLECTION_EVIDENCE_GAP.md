# COD Cash-Collection Evidence Gap

Review date: 2026-11-23  
Scope: read-only inspection of the development branch `agent/supabase-integration`.

## Finding

The current order workflow has a delivery-status event, but no distinct cash-collection confirmation event. Therefore `delivered` is not an acceptable source event for loyalty-point grants or for recording collected cash.

## Evidence in the current implementation

1. Checkout saves the order and tells the buyer that payment is cash on delivery. The order is created before fulfilment and does not carry a collection receipt/reference.
2. The shipment state machine allows a seller or administrator to move a shipment from `shipped` to `delivered`.
3. `change_status()` appends an `order_events` row for that status transition.
4. On `delivered`, the same handler inserts a `cod_commission_due` entry into the immutable seller ledger. The code comment explicitly states that this is not proof of cash collection and does not create a seller payout.
5. The seller ledger entry stores a delivery snapshot and is protected against mutation; its semantics are commission due, not cash received.
6. The inspected order, seller, and admin route files contain no separate COD collection-confirmation endpoint or collection receipt workflow. The admin summary reports the commission-due aggregate; it does not establish collected cash.

## Consequence for loyalty

A future grant must not be triggered by any of these alone:

- order creation;
- seller status `delivered`;
- an order event note;
- seller commission-due ledger insertion;
- an unverified client-provided boolean or timestamp.

The current repository does not provide sufficient evidence to safely issue points for COD orders. This is a hard implementation gate, not a temporary assumption.

## Minimum future collection event contract

Before enabling COD-based grants, the product owner must approve who may confirm collection and what evidence is required. Engineering should then introduce a distinct, auditable collection event with:

- shipment/order reference bound server-side;
- actor identity and role validated by server authorization;
- amount and currency/unit represented in integer fils;
- collection timestamp assigned by the server;
- evidence/reference identifier appropriate to the approved fulfilment process;
- idempotency key and uniqueness preventing duplicate confirmation;
- append-only audit semantics and a correction/reversal process that never edits the original event;
- explicit separation between collected cash, seller commission due, platform commission collected, and seller payout;
- tests proving seller A cannot confirm seller B's shipment, buyer cannot self-confirm, duplicate retries cannot double-confirm, and a delivery event without collection evidence cannot grant points.

The exact evidence type, confirmer role, and exception/reversal authority are operational policy decisions and must not be invented by engineering.

## Safe disposition

- Keep the loyalty ledger, grant API, and buyer-facing available-points balance unimplemented.
- Do not infer a cash receipt from delivery or commission ledger data.
- Do not change the current COD, commission, coupon, order-total, or payout behavior as part of this review.
- Reopen this gate only after the owner approves the collection process and the resulting authorization model can be tested end-to-end.

This review is documentation only. It adds no database objects, grants, endpoints, or production changes.
