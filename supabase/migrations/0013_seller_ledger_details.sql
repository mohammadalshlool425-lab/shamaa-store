-- Expand COD commission entries into referenced, immutable journal records.
-- This migration changes only the schema; it does not enable RLS policies or grants.
BEGIN;

ALTER TABLE shamaa_app.ledger
  ADD COLUMN source TEXT NOT NULL DEFAULT 'cod_delivery'
    CHECK (source='cod_delivery'),
  ADD COLUMN reference_type TEXT NOT NULL DEFAULT 'seller_order'
    CHECK (reference_type='seller_order'),
  ADD COLUMN reference_id BIGINT NOT NULL DEFAULT 1,
  ADD COLUMN basis_fils BIGINT NOT NULL DEFAULT 0
    CHECK (basis_fils>=0),
  ADD COLUMN snapshot_json TEXT NOT NULL DEFAULT '[]'
    CHECK (jsonb_typeof(snapshot_json::jsonb)='array');

DROP TRIGGER immutable_ledger_update ON shamaa_app.ledger;

UPDATE shamaa_app.ledger l
   SET reference_id=l.seller_order_id,
       basis_fils=COALESCE((
         SELECT SUM(oi.unit_price_fils*oi.quantity)
           FROM shamaa_app.order_items oi WHERE oi.seller_order_id=l.seller_order_id
       ),0),
       snapshot_json=COALESCE((
         SELECT json_agg(json_build_object(
           'order_item_id',oi.id,'product_id',oi.product_id,
           'title_ar',oi.title_ar,'title_en',oi.title_en,
           'quantity',oi.quantity,'unit_price_fils',oi.unit_price_fils,
           'commission_bps',oi.commission_bps,'commission_fils',oi.commission_fils
         ))::text
           FROM shamaa_app.order_items oi WHERE oi.seller_order_id=l.seller_order_id
       ),'[]');

ALTER TABLE shamaa_app.ledger
  ADD CONSTRAINT ledger_reference_matches_shipment
    CHECK (reference_id=seller_order_id);

CREATE INDEX ledger_store_order ON shamaa_app.ledger(store_id,id DESC);

CREATE OR REPLACE FUNCTION shamaa_app.guard_ledger_delivery_reference()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
  IF NEW.reference_id <> NEW.seller_order_id
     OR NOT EXISTS (
       SELECT 1 FROM shamaa_app.seller_orders so
        WHERE so.id=NEW.seller_order_id AND so.store_id=NEW.store_id
          AND so.status='delivered' AND so.commission_fils=NEW.amount_fils
          AND NEW.basis_fils=COALESCE((
            SELECT SUM(oi.unit_price_fils*oi.quantity)
              FROM shamaa_app.order_items oi WHERE oi.seller_order_id=so.id
          ),0)
     )
     OR NOT EXISTS (
       SELECT 1 FROM shamaa_app.order_events oe
        WHERE oe.seller_order_id=NEW.seller_order_id AND oe.to_status='delivered'
     ) THEN
    RAISE EXCEPTION 'ledger entry requires a matching delivered shipment snapshot'
      USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER ledger_delivery_reference_guard
BEFORE INSERT ON shamaa_app.ledger
FOR EACH ROW EXECUTE FUNCTION shamaa_app.guard_ledger_delivery_reference();

COMMIT;
