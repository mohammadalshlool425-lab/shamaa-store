-- Store-funded coupon discounts must be reflected in immutable order totals.
BEGIN;

DO $$
DECLARE
  old_constraint TEXT;
  matches INTEGER;
BEGIN
  SELECT count(*), min(conname)
    INTO matches, old_constraint
    FROM pg_constraint
   WHERE conrelid='shamaa_app.orders'::regclass
     AND contype='c'
     AND pg_get_constraintdef(oid) LIKE '%total_fils%'
     AND pg_get_constraintdef(oid) LIKE '%subtotal_fils%'
     AND pg_get_constraintdef(oid) LIKE '%shipping_fils%'
     AND pg_get_constraintdef(oid) NOT LIKE '%discount_fils%';
  IF matches <> 1 THEN
    RAISE EXCEPTION 'Expected one pre-coupon orders total constraint; found %', matches;
  END IF;
  EXECUTE format('ALTER TABLE shamaa_app.orders DROP CONSTRAINT %I', old_constraint);

  SELECT count(*), min(conname)
    INTO matches, old_constraint
    FROM pg_constraint
   WHERE conrelid='shamaa_app.seller_orders'::regclass
     AND contype='c'
     AND pg_get_constraintdef(oid) LIKE '%net_fils%'
     AND pg_get_constraintdef(oid) LIKE '%subtotal_fils%'
     AND pg_get_constraintdef(oid) LIKE '%shipping_fils%'
     AND pg_get_constraintdef(oid) LIKE '%commission_fils%'
     AND pg_get_constraintdef(oid) NOT LIKE '%discount_fils%';
  IF matches <> 1 THEN
    RAISE EXCEPTION 'Expected one pre-coupon seller-order net constraint; found %', matches;
  END IF;
  EXECUTE format('ALTER TABLE shamaa_app.seller_orders DROP CONSTRAINT %I', old_constraint);
END $$;

ALTER TABLE shamaa_app.orders
  ADD CONSTRAINT orders_discount_aware_total_check
  CHECK (total_fils = subtotal_fils + shipping_fils - discount_fils);
ALTER TABLE shamaa_app.seller_orders
  ADD CONSTRAINT seller_orders_discount_aware_net_check
  CHECK (net_fils = subtotal_fils + shipping_fils - discount_fils - commission_fils);

COMMIT;
