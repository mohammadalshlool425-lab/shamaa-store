-- SHAM'A phase 2, batch 1: disclose used-item condition and preserve it per order line.
-- No role, grant, RLS policy, or Data API exposure is changed by this migration.
BEGIN;
ALTER TABLE shamaa_app.products ADD COLUMN IF NOT EXISTS condition TEXT NOT NULL DEFAULT 'new' CHECK (condition IN ('new','used'));
ALTER TABLE shamaa_app.products ADD COLUMN IF NOT EXISTS condition_note TEXT NOT NULL DEFAULT '';
ALTER TABLE shamaa_app.order_items ADD COLUMN IF NOT EXISTS condition TEXT NOT NULL DEFAULT 'new' CHECK (condition IN ('new','used'));
ALTER TABLE shamaa_app.order_items ADD COLUMN IF NOT EXISTS condition_note TEXT NOT NULL DEFAULT '';
COMMIT;
