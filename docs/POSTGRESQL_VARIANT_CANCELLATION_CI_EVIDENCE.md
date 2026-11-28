# PostgreSQL variant cancellation CI evidence

## Verified run

- Repository: `mohammadalshlool425-lab/shamaa-store`
- Development branch: `agent/supabase-integration`
- Tested commit: `cea94959817ff853ba822920710ab405067e7a61`
- GitHub Actions run: `45281322599` (run 797)
- Result: all three jobs completed successfully.

## Specific test inclusion

The PostgreSQL 17 job explicitly ran the workflow step:

`Run concurrent variant cancellation test on PostgreSQL`

The step is configured to execute:

`python -m pytest -q tests/test_postgres_variant_cancellation.py`

The step completed with conclusion `success`. This closes the previous evidence gap where the test file existed but was not part of the workflow command.

## Assertions covered by the test

The test creates isolated seller, buyer, product, and variant records in the CI PostgreSQL database, checks out quantity two, and races two buyer cancellation requests against the same shipment. It asserts:

- Exactly one request returns HTTP 200 and the other HTTP 409.
- Parent product stock and variant stock both return to 3.
- Exactly two inventory movements exist for the shipment: `sale_variant_<id>` with delta -2 and `cancel_variant_<id>` with delta +2.
- Exactly one cancellation event exists.

## Other CI results

All three workflow jobs succeeded:

1. PostgreSQL 17 schema and security contract, including migrations 0001–0013, RLS deny-all and temporary session/audit policy contracts, adapter integration, application smoke tests, and the variant cancellation step.
2. Server, permissions, stock, encrypted restore, syntax, backend tests, coverage, and dependency audit.
3. Bilingual mobile checkout, seller/admin, accessibility, and offline browser scenarios (nine Chromium scenarios).

## Scope and limitations

- PostgreSQL is an isolated CI database. This run does not apply migrations to the live Supabase project.
- The PostgreSQL application tests use the CI database owner; they do not prove route isolation under the future restricted runtime role.
- The run does not authorize merging PR #3, deployment, production credential changes, financial policy changes, or live database operations.
- PR #3 remains open and unmerged.

## Next security work

Continue with buyer-vs-buyer and seller-vs-seller isolation tests for orders, conversations, cases, private attachments, and returns, including guessed identifiers and cross-store shipment identifiers. Keep restricted-role policy/grant design separate from the passing CI concurrency evidence.
