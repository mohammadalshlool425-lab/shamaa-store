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

## Payment-source boundary checked in the same review

The checkout schema and database foundation were also inspected to avoid assuming that an online payment source already exists:

- The checkout request schema has no client-selectable payment-method field.
- The foundation migration constrains `orders.payment_method` to `cod` with a database CHECK constraint and default.
- The inspected checkout route creates the order without a payment-provider reference, provider transaction identifier, authorization/capture state, or verified webhook event.
- Therefore the current repository has no implemented online-payment confirmation source either. A future payment integration would need its own provider-verified, server-side event contract; a browser redirect or client-supplied “paid” flag must not be accepted as proof.

This narrows the present loyalty gate: **there is currently no authoritative payment-settlement event for any order type**. For COD, delivery remains distinct from cash collection. For any future electronic method, provider settlement must be independently verified and idempotent before it can be considered an earning event. No payment method, checkout behavior, or financial record was changed by this review.



## Return and refund boundary checked in the same review

The item-return workflow was inspected in `server/routes/returns.py` and `tests/test_returns.py`:

- A return can be opened only for a delivered order item within the configured 14-day operational window. Seller approval, buyer shipment, seller receipt, and an MFA-rechecked administrator decision are separate workflow steps.
- The return state machine and append-only event history record operational decisions. The final `resolved` state is not a payment refund, cash receipt, or settlement event.
- The return endpoints do not create a refund transaction, payment-provider reversal, cash repayment record, inventory restock, or commission-ledger reversal.
- Existing acceptance tests verify that after a return is resolved, the seller-order ledger still contains only the original COD commission-due entry and the original shipment remains `delivered`. This is deliberate separation, not evidence that a refund has been paid.
- Consequently, a resolved return must not itself trigger loyalty reversal, customer refund confirmation, or seller commission adjustment. Those actions require distinct, authorized, idempotent financial events with their own evidence and reconciliation.

**Open financial workflow gap:** the repository currently records return disposition but does not implement the financial consequences of an approved refund. Before building that workflow, the owner must decide the refund payer/source, who authorizes it, evidence of money returned, partial-refund calculation, treatment of delivery fees and discounts, inventory disposition, and how/when the seller commission is reversed or reconciled. Engineering must not infer these policies from the return status.

This was a read-only code and test review. No return, inventory, ledger, payment, or production behavior was changed.
