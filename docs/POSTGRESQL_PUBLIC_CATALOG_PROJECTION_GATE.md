# Public Catalog Projection and RLS Gate

Status: **blocking design review; no policies or grants are authorized by this document.**
Reviewed on 2026-11-24 against `agent/supabase-integration`.

## Finding

The public catalog handlers currently depend on broad row reads and remove private fields only after fetching them into Python. That is safe only while the database connection is a trusted server credential. It is not a safe basis for granting a restricted runtime role broad SELECT on the underlying tables or exposing those tables through a client-facing Data API.

This is a database projection problem, not evidence of a currently exploitable public endpoint. The PostgreSQL runtime path is not yet wired in `create_app()`, and migrations 0001–0013 do not grant application-facing table access.

## Confirmed call sites

| Public handler | SQL shape | Fields fetched before Python filtering | Required public response |
|---|---|---|---|
| `GET /api/bootstrap` | `SELECT c.*` and `SELECT key,value FROM settings` | Category commission fields; every setting key/value | Category display fields + computed product count; only approved `home_*` content |
| `GET /api/products` and detail | shared `PRODUCT_SQL` selects `p.*`, store owner/status/shipping/commission fields and category commission fields | Product moderation/SKU/version/internal media fields; store owner id and commission; category commission | Explicit product card/detail projection; public store/category display data; only active variants |
| `GET /api/stores` and detail | `SELECT s.*` | Store owner id, review note/status, commission configuration and any future private columns | Explicit public store profile fields + active product count |
| Product bestseller sort | correlated subquery over `order_items` and `seller_orders` | Delivered sales aggregation | A narrowly scoped aggregate, not public access to order items or shipment rows |
| Public product review endpoint | explicit approved review columns | No buyer identity in response projection | Approved review body/rating/date only |

The response serializers `product_dict()` and `store_dict()` remove some fields after retrieval. In `bootstrap()`, category commission is removed with `pop` and settings are filtered to `home_*` after the full settings result is fetched. These serializers do not constrain SQL privileges or direct database access.

## Security consequences

1. Do not grant public/runtime SELECT on `settings`, `stores`, `products`, or `categories` merely because a public HTTP endpoint reads them.
2. Do not expose `order_items` or `seller_orders` to anonymous users to support the bestseller sort.
3. A row-level policy does not hide columns. If the runtime role has table-level SELECT, it can select columns omitted by the HTTP serializer.
4. Do not assume `security_barrier` alone is a column-privilege boundary. The public role must receive SELECT only on the approved view columns and no underlying-table SELECT.
5. Keep private media separate: a public product image reference must not imply that arbitrary media metadata or original uploads are public.

## Required implementation design before runtime grants

- Introduce explicit public projection views (or equivalent least-privilege SQL functions) for:
  - bootstrap categories and approved home content;
  - active product cards/details and active variants;
  - active store profiles;
  - public approved review aggregates and rows;
  - bestseller ranking aggregate.
- Define each view with explicit column lists; never use `SELECT *` in a public projection.
- Ensure public projections enforce active product, active store, and active owner predicates at the database boundary.
- Grant the anonymous/public database role SELECT only on those projection objects and only on their exposed columns. Revoke underlying table SELECT.
- Keep buyer/seller/admin routes on separate owner-scoped policies; a public projection must not become a shortcut for private seller or buyer data.
- Rewrite application SQL to use the projection objects where appropriate, then test the HTTP response and direct SQL column-denial behavior under the actual restricted role.
- Test that bestseller output can be computed without granting access to raw order items, order identifiers, buyer identifiers, or shipment rows.
- Reconcile view ownership, `security_invoker` behavior, schema usage, and RLS semantics with the deployed PostgreSQL version before choosing view syntax.

## Acceptance tests

1. Anonymous/public role can read approved public product/store/category fields.
2. The same role receives permission denied when selecting `owner_id`, commission configuration, moderation notes, SKU/version fields, private settings, buyer identifiers, raw order items, or shipment rows.
3. Inactive owner/store/product rows never appear through projections.
4. Seller A cannot use public projections to read seller B's drafts, media metadata, variants, or private fields.
5. Bestseller ranking returns only an aggregate/rank and does not expose raw order or buyer data.
6. The full API and PostgreSQL suite passes under the restricted role; CI owner/superuser runs do not count as proof of runtime authorization.

## Scope and safety

This is a static review and design gate only. No SQL migration, view, grant, role, endpoint, runtime connection setting, production credential, live Supabase change, merge, or deployment was created or changed.
