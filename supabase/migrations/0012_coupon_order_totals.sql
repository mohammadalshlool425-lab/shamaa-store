-- Store-funded coupon discounts must be reflected in immutable order totals.
BEGIN;
ALTER TABLE shamaa_app.orders
  DROP CONSTRAINT orders_total_fils_check;
ALTER TABLE shamaa_app.orders
  ADD CONSTRAINT orders_total_fils_check
  CHECK (total_fils = subtotal_fils + shipping_fils - discount_fils);
ALTER TABLE shamaa_app.seller_orders
  DROP CONSTRAINT seller_orders_net_fils_check;
ALTER TABLE shamaa_app.seller_orders
  ADD CONSTRAINT seller_orders_net_fils_check
  CHECK (net_fils = subtotal_fils + shipping_fils - discount_fils - commission_fils);
COMMIT;
