-- Expand COD commission entries into referenced, immutable journal records.
-- Historical entries are backfilled from their delivered shipment and order items.
ALTER TABLE ledger ADD COLUMN source TEXT NOT NULL DEFAULT 'cod_delivery'
  CHECK (source='cod_delivery');
ALTER TABLE ledger ADD COLUMN reference_type TEXT NOT NULL DEFAULT 'seller_order'
  CHECK (reference_type='seller_order');
ALTER TABLE ledger ADD COLUMN reference_id INTEGER NOT NULL DEFAULT 1
  CHECK (reference_id>0);
ALTER TABLE ledger ADD COLUMN basis_fils INTEGER NOT NULL DEFAULT 0
  CHECK (basis_fils>=0);
ALTER TABLE ledger ADD COLUMN snapshot_json TEXT NOT NULL DEFAULT '[]';

DROP TRIGGER IF EXISTS immutable_ledger_update;
DROP TRIGGER IF EXISTS immutable_ledger_delete;

UPDATE ledger
   SET reference_id=seller_order_id,
       basis_fils=COALESCE((
         SELECT SUM(oi.unit_price_fils*oi.quantity)
           FROM order_items oi WHERE oi.seller_order_id=ledger.seller_order_id
       ),0),
       snapshot_json=COALESCE((
         SELECT json_group_array(json_object(
           'order_item_id',oi.id,'product_id',oi.product_id,
           'title_ar',oi.title_ar,'title_en',oi.title_en,
           'quantity',oi.quantity,'unit_price_fils',oi.unit_price_fils,
           'commission_bps',oi.commission_bps,'commission_fils',oi.commission_fils
         ))
           FROM order_items oi WHERE oi.seller_order_id=ledger.seller_order_id
       ),'[]');

CREATE TRIGGER immutable_ledger_update BEFORE UPDATE ON ledger
BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER immutable_ledger_delete BEFORE DELETE ON ledger
BEGIN SELECT RAISE(ABORT,'append-only'); END;

CREATE INDEX ledger_store_order ON ledger(store_id,id DESC);

CREATE TRIGGER ledger_delivery_reference_guard
BEFORE INSERT ON ledger
WHEN NEW.reference_id<>NEW.seller_order_id
 OR NOT EXISTS (
   SELECT 1 FROM seller_orders so
    WHERE so.id=NEW.seller_order_id AND so.store_id=NEW.store_id
      AND so.status='delivered' AND so.commission_fils=NEW.amount_fils
      AND NEW.basis_fils=COALESCE((
        SELECT SUM(oi.unit_price_fils*oi.quantity)
          FROM order_items oi WHERE oi.seller_order_id=so.id
      ),0)
 )
 OR NOT EXISTS (
   SELECT 1 FROM order_events oe
    WHERE oe.seller_order_id=NEW.seller_order_id AND oe.to_status='delivered'
 )
BEGIN
  SELECT RAISE(ABORT,'ledger entry requires a matching delivered shipment snapshot');
END;
