-- Product media gallery. Primary image is position zero and is migrated from media_id.
CREATE TABLE product_images (
  id INTEGER PRIMARY KEY,
  product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  media_id INTEGER NOT NULL REFERENCES media(id),
  position INTEGER NOT NULL CHECK(position BETWEEN 0 AND 7),
  created_at INTEGER NOT NULL,
  UNIQUE(product_id,media_id),
  UNIQUE(product_id,position)
);
INSERT INTO product_images(product_id,media_id,position,created_at)
SELECT id,media_id,0,created_at FROM products WHERE media_id IS NOT NULL;
