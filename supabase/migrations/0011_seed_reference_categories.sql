-- SHAM'A phase 3: immutable reference catalog, installed out-of-band with schema migrations.
-- Runtime startup must not need category INSERT privileges.
BEGIN;

INSERT INTO shamaa_app.categories(id,name_ar,name_en,icon,parent_id) VALUES
 ('electronics','الإلكترونيات','Electronics','headphones',NULL),
 ('audio','الصوتيات','Audio','headphones','electronics'),
 ('fashion','الأزياء والموضة','Fashion','shirt',NULL),
 ('home','المنزل والمعيشة','Home & living','lamp',NULL),
 ('beauty','العناية والجمال','Beauty & care','sparkles',NULL),
 ('sports','الرياضة','Sports','dumbbell',NULL),
 ('handmade','صُنع بحب','Handmade','leaf',NULL),
 ('books','الكتب والقرطاسية','Books & stationery','book',NULL),
 ('accessories','الإكسسوارات','Accessories','watch',NULL)
ON CONFLICT (id) DO NOTHING;

COMMIT;
