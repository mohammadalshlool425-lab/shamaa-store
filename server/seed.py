"""Reference categories always; illustrative products ONLY on explicit opt-in.
No seeded login credentials, reviews, sales counts or trusted-seller badges.
"""
import secrets
from .database import now
from .security import hash_password

CATEGORIES = [
 ('electronics','الإلكترونيات','Electronics','headphones',None),
 ('audio','الصوتيات','Audio','headphones','electronics'),
 ('fashion','الأزياء والموضة','Fashion','shirt',None),
 ('home','المنزل والمعيشة','Home & living','lamp',None),
 ('beauty','العناية والجمال','Beauty & care','sparkles',None),
 ('sports','الرياضة','Sports','dumbbell',None),
 ('handmade','صُنع بحب','Handmade','leaf',None),
 ('books','الكتب والقرطاسية','Books & stationery','book',None),
 ('accessories','الإكسسوارات','Accessories','watch',None),
]
STORES = [
 ('bayt','بيت للأشياء الجميلة','Bayt Living','تفاصيل بسيطة، تصنع بيتًا أجمل.','Little details for a lovely home.','amman','green'),
 ('daily','ديلي ستايل','Daily Style','اختيارات ترافق تفاصيل يومك.','Thoughtful essentials for every day.','irbid','sand'),
 ('nawa','نواة','Nawa Studio','أشياء صُنعت بعناية، لتدوم معك.','Considered objects, made to stay.','amman','blue'),
]
# All specs are illustrative and never represent actual goods or guarantees.
PRODUCTS = [
 ('سماعات لاسلكية بلون عاجي','Ivory wireless headphones','audio',45000,59000,12,'headphones',1,
  'منتج توضيحي: سماعات بتصميم فوق الأذن ولون عاجي. المواصفات والصور للتجربة وليست عرض بيع حقيقي.',
  'Illustrative product: ivory over-ear headphones. Images and specifications are for preview, not a real sales offer.'),
 ('حذاء رياضي بتصميم يومي','Everyday neutral sneakers','fashion',38500,48000,8,'shoes',1,
  'منتج توضيحي: حذاء بلون كريمي وتفاصيل بيج، مقاس 40. بيانات للمعاينة فقط.',
  'Illustrative product: cream and beige everyday sneakers, size 40. Preview data only.'),
 ('حقيبة يد بلون الكراميل','Caramel everyday handbag','fashion',32000,None,6,'bag',1,
  'منتج توضيحي: حقيبة يد بنية بتصميم بسيط وحزام طويل. بيانات وصورة مولّدة للمعاينة فقط.',
  'Illustrative product: a brown handbag with simple handles and a long strap. Preview image and data only.'),
 ('مصباح طاولة بلون الميرمية','Sage mushroom table lamp','home',24500,29000,10,'lamp',0,
  'منتج توضيحي: مصباح طاولة صغير بلون أخضر هادئ. صورة وبيانات توضيحية، وليس منتجًا متاحًا للشحن الفعلي.',
  'Illustrative product: a small sage green table lamp. This is preview data, not available for real shipment.'),
 ('كوب سفر حراري أخضر','Forest green travel cup','home',14000,18000,15,'coffee',0,
  'منتج توضيحي: كوب سفر بغطاء مغلق بلون أخضر داكن. لا نقدّم ضمانًا للسعة أو مدة حفظ الحرارة.',
  'Illustrative product: a dark green travel cup with a closed lid. No capacity or thermal performance is claimed.'),
 ('ساعة كلاسيكية بحزام بني','Classic brown-strap watch','accessories',49000,None,5,'watch',2,
  'منتج توضيحي: ساعة بعقارب وقرص كريمي وحزام بني. لا توجد ادعاءات بالأصالة أو مقاومة الماء.',
  'Illustrative product: an analog watch with a cream dial and brown strap. No authenticity or water-resistance claims.'),
 ('كوب خزفي بتفاصيل طبيعية','Handcrafted ceramic cup','handmade',12000,None,7,'ceramics',2,
  'منتج توضيحي: كوب خزفي بلون رملي وتفاصيل نقطية. الصورة والمواصفات للعرض التجريبي فقط.',
  'Illustrative product: a sand-toned speckled ceramic cup. Image and details are for preview only.'),
 ('مجموعة عبوات للعناية اليومية','Daily care bottle set','beauty',22000,27000,9,'skincare',2,
  'منتج توضيحي: عبوات زجاجية للعناية. لا تتضمن هذه البيانات مكونات أو فوائد صحية أو محتوى تجميليًا معتمدًا.',
  'Illustrative care bottles. No ingredients, health benefits or certified cosmetic contents are represented.'),
]


def initialize_catalog(db, settings):
    with db.transaction() as c:
        for row in CATEGORIES:
            c.execute('INSERT OR IGNORE INTO categories(id,name_ar,name_en,icon,parent_id) VALUES(?,?,?,?,?)',row)
    if not settings.demo:
        return
    with db.read() as c:
        if c.execute('SELECT COUNT(*) FROM products').fetchone()[0]:
            return
    unusable = hash_password(secrets.token_urlsafe(64))
    with db.transaction() as c:
        if c.execute('SELECT COUNT(*) FROM products').fetchone()[0]:
            return
        store_ids = []
        for index,(slug,ar,en,desc_ar,desc_en,city,accent) in enumerate(STORES):
            uid = c.execute('INSERT INTO users(name,email,password_hash,role,created_at) VALUES(?,?,?,?,?)',
                            (en,f'preview-{index}@example.invalid',unusable,'seller',now())).lastrowid
            sid = c.execute('''INSERT INTO stores(owner_id,slug,name_ar,name_en,description_ar,description_en,city,accent,
              shipping_fils,status,is_demo,created_at) VALUES(?,?,?,?,?,?,?,?,?,'active',1,?)''',
              (uid,slug,ar,en,desc_ar,desc_en,city,accent,2500+index*500,now())).lastrowid
            store_ids.append(sid)
        for i,(ar,en,cat,price,original,stock,image,store,desc_ar,desc_en) in enumerate(PRODUCTS):
            c.execute('''INSERT INTO products(store_id,category_id,title_ar,title_en,description_ar,description_en,price_fils,
              compare_at_fils,stock,sku,demo_image,status,featured,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,'active',1,?,?)''',
              (store_ids[store],cat,ar,en,desc_ar,desc_en,price,original,stock,f'PREVIEW-{i+1}',image,now()-i*3600,now()))
