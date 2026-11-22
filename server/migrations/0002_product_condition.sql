-- Add explicit new/used state to physical listings and immutable order-line snapshots.
ALTER TABLE products ADD COLUMN condition TEXT NOT NULL DEFAULT 'new' CHECK (condition IN ('new','used'));
ALTER TABLE products ADD COLUMN condition_note TEXT NOT NULL DEFAULT '';
ALTER TABLE order_items ADD COLUMN condition TEXT NOT NULL DEFAULT 'new' CHECK (condition IN ('new','used'));
ALTER TABLE order_items ADD COLUMN condition_note TEXT NOT NULL DEFAULT '';
