"""Money is integer fils (1 JOD = 1000 fils), never floating point."""
import hashlib
import json
from .errors import fail

CITIES = [
 ('amman','عمّان','Amman'),('irbid','إربد','Irbid'),('zarqa','الزرقاء','Zarqa'),
 ('aqaba','العقبة','Aqaba'),('salt','السلط','Salt'),('madaba','مأدبا','Madaba'),
 ('jerash','جرش','Jerash'),('ajloun','عجلون','Ajloun'),('mafraq','المفرق','Mafraq'),
 ('karak','الكرك','Karak'),('tafilah','الطفيلة','Tafilah'),('maan','معان','Ma’an'),('ramtha','الرمثا','Ramtha'),
]
PRODUCT_SQL = '''SELECT p.*,s.name_ar AS store_name_ar,s.name_en AS store_name_en,s.slug AS store_slug,
 s.city,s.status AS store_status,s.owner_id,s.shipping_fils,s.delivery_days,s.is_demo,s.commission_bps AS store_commission_bps,
 c.name_ar AS category_name_ar,c.name_en AS category_name_en,c.parent_id,c.commission_bps AS category_commission_bps,
 (SELECT COUNT(*) FROM product_variants pv WHERE pv.product_id=p.id AND pv.active=TRUE) AS active_variant_count,
 (SELECT COUNT(*) FROM product_variants pv WHERE pv.product_id=p.id) AS variant_count
 FROM products p JOIN stores s ON s.id=p.store_id JOIN users u ON u.id=s.owner_id
 JOIN categories c ON c.id=p.category_id'''
PUBLIC_WHERE = "p.status='active' AND s.status='active' AND u.active=TRUE"
DEMO_IMAGES = {'headphones','shoes','bag','lamp','coffee','watch','ceramics','skincare'}


def product_image(p):
    if p['media_id']:
        return f"/media/{p['media_id']}/480.webp"
    if p['demo_image'] in DEMO_IMAGES:
        return f"/static/images/{p['demo_image']}.webp"
    return '/static/images/placeholder.svg'


def product_dict(row, private=False):
    p = dict(row)
    keys = ('id','store_id','category_id','title_ar','title_en','description_ar','description_en',
            'price_fils','compare_at_fils','stock','condition','condition_note','store_name_ar','store_name_en','store_slug',
            'city','shipping_fils','delivery_days','is_demo','category_name_ar','category_name_en','featured','variant_count')
    result = {k:p[k] for k in keys if k in p}
    result['image_url'] = product_image(p)
    result['has_variants'] = int(p.get('active_variant_count') or 0) > 0
    if private:
        result.update({k:p[k] for k in ('status','review_note','sku','media_id','updated_at','version')})
    return result


def store_dict(row, private=False):
    keys = ['id','slug','name_ar','name_en','description_ar','description_en','city','accent','shipping_fils','delivery_days','is_demo']
    if private:
        keys += ['status','review_note','commission_bps']
    return {k:row[k] for k in keys}


def canonical_hash(obj):
    return hashlib.sha256(json.dumps(obj,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def commission_rate(c, p):
    if p['store_commission_bps'] is not None:
        return p['store_commission_bps']
    if p['category_commission_bps'] is not None:
        return p['category_commission_bps']
    if p['parent_id']:
        parent = c.execute('SELECT commission_bps FROM categories WHERE id=?',(p['parent_id'],)).fetchone()
        if parent and parent[0] is not None:
            return parent[0]
    return int(c.execute("SELECT value FROM settings WHERE key='commission_bps'").fetchone()[0])


def commission(amount_fils, bps):
    return (amount_fils * bps + 5000) // 10000


def cart_quote(c, user_id, strict=False):
    rows = c.execute(
        "SELECT product_rows.*, ca.quantity AS cart_quantity, ca.variant_id AS cart_variant_id, "
        "pv.sku AS cart_variant_sku, pv.attributes_json AS cart_variant_attributes_json, "
        "pv.price_fils AS cart_variant_price_fils, pv.stock AS cart_variant_stock, "
        "pv.active AS cart_variant_active "
        "FROM (" + PRODUCT_SQL + ") AS product_rows "
        "JOIN carts ca ON ca.product_id=product_rows.id "
        "LEFT JOIN product_variants pv ON pv.id=ca.variant_id "
        "WHERE ca.user_id=? ORDER BY product_rows.store_id,product_rows.id,ca.variant_key",
        (user_id,),
    ).fetchall()
    groups = {}
    total_quantity = 0
    for r in rows:
        p = dict(r)
        qty = int(p['cart_quantity'])
        variant_id = p['cart_variant_id']
        has_variants = int(p.get('variant_count') or 0) > 0
        available = p['status']=='active' and p['store_status']=='active'
        owner = c.execute('SELECT active FROM users WHERE id=?',(p['owner_id'],)).fetchone()
        available = available and bool(owner['active'])
        attributes = None
        variant_sku = None
        if variant_id is not None:
            variant_active = bool(p['cart_variant_active'])
            available = available and variant_active
            unit_price = int(p['cart_variant_price_fils'])
            stock = int(p['cart_variant_stock'])
            attributes = json.loads(p['cart_variant_attributes_json'])
            variant_sku = p['cart_variant_sku']
        else:
            # Variant-configured products must never silently use their legacy base stock.
            available = available and not has_variants
            unit_price = int(p['price_fils'])
            stock = int(p['stock'])
        if strict and (not available or qty > stock):
            fail('stock',409)
        if strict and p['owner_id'] == user_id:
            fail('own_product',409)
        g = groups.setdefault(p['store_id'],{
            'store_id':p['store_id'],'name_ar':p['store_name_ar'],'name_en':p['store_name_en'],
            'slug':p['store_slug'],'shipping_fils':p['shipping_fils'],'delivery_days':p['delivery_days'],
            'subtotal_fils':0,'items':[], 'is_demo':p['is_demo'],
        })
        item = product_dict(p)
        item.update(
            quantity=qty,available=bool(available),stock=stock,
            price_fils=unit_price,unit_price_fils=unit_price,
            variant_id=variant_id,variant_sku=variant_sku,variant_attributes=attributes,
            variant_label=' · '.join(str(value) for value in attributes.values()) if attributes else '',
            line_total_fils=unit_price*qty,
        )
        g['items'].append(item)
        g['subtotal_fils'] += item['line_total_fils']
        total_quantity += qty
    groups = list(groups.values())
    subtotal = sum(g['subtotal_fils'] for g in groups)
    shipping = sum(g['shipping_fils'] for g in groups)
    result = {'groups':groups,'subtotal_fils':subtotal,'shipping_fils':shipping,'total_fils':subtotal+shipping,
              'quantity':total_quantity}
    # Binds chosen variant, SKU attributes, price, availability and each shipment fee.
    result['quote_hash'] = canonical_hash(result)
    return result
