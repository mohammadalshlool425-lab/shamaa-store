import hashlib
import json
from io import BytesIO
import os
import secrets
import warnings
from threading import BoundedSemaphore
from fastapi import APIRouter, BackgroundTasks, Depends, File, Request, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from ..commerce import PRODUCT_SQL, product_dict, store_dict
from ..database import DatabaseIntegrityError, insert_id, now
from ..errors import fail
from .. import mail, schemas
from ..security import audit, seller_user, throttle, verified_user

router = APIRouter(prefix='/api/seller', tags=['Seller'])
Image.MAX_IMAGE_PIXELS = 16_000_000
IMAGE_JOBS = BoundedSemaphore(2)


def own_store(c, user, required=True):
    row = c.execute('SELECT * FROM stores WHERE owner_id=?',(user['id'],)).fetchone()
    if required and not row:
        fail('not_found',404)
    return row


@router.get('/store')
def my_store(request: Request,user=Depends(verified_user)):
    with request.app.state.db.read() as c:
        row = own_store(c,user,False)
    return {'store':store_dict(row,True) if row else None}


@router.post('/store', status_code=201)
def create_store(body: schemas.StoreInput,request: Request,user=Depends(verified_user)):
    if user['role'] not in ('buyer','seller'):
        fail('forbidden',403)
    throttle(request,'store',5,3600,str(user['id']))
    try:
        with request.app.state.db.transaction() as c:
            data = body.model_dump()
            keys = ','.join(data)
            marks = ','.join('?' for _ in data)
            store_id = insert_id(c, f'INSERT INTO stores(owner_id,{keys},created_at) VALUES(?,{marks},?)',
                                 [user['id'],*data.values(),now()])
            c.execute("UPDATE users SET role='seller' WHERE id=?",(user['id'],))
            audit(c,user['id'],'store_requested','store',store_id)
            row = c.execute('SELECT * FROM stores WHERE id=?',(store_id,)).fetchone()
    except DatabaseIntegrityError:
        fail('store_exists',409)
    return {'store':store_dict(row,True)}


@router.put('/store')
def edit_store(body: schemas.StoreInput,request: Request,user=Depends(seller_user)):
    try:
        with request.app.state.db.transaction() as c:
            store = own_store(c,user)
            identity_fields = ('slug','name_ar','name_en','description_ar','description_en','city')
            changed = any(store[k] != getattr(body,k) for k in identity_fields)
            data = body.model_dump()
            status = 'pending' if changed else store['status']
            c.execute('UPDATE stores SET '+','.join(k+'=?' for k in data)+',status=? WHERE id=?',
                      [*data.values(),status,store['id']])
            audit(c,user['id'],'store_updated','store',store['id'])
            row = c.execute('SELECT * FROM stores WHERE id=?',(store['id'],)).fetchone()
    except DatabaseIntegrityError:
        fail('store_exists',409)
    return {'store':store_dict(row,True)}


@router.get('/products')
def my_products(request: Request,user=Depends(seller_user)):
    with request.app.state.db.read() as c:
        store = own_store(c,user)
        rows = c.execute(PRODUCT_SQL+' WHERE p.store_id=? ORDER BY p.id DESC',(store['id'],)).fetchall()
    result = []
    for row in rows:
        item = product_dict(row,True)
        variants = c.execute("SELECT * FROM product_variants WHERE product_id=? ORDER BY id",(row['id'],)).fetchall()
        item['variants'] = [variant_dict(v,True) for v in variants]
        result.append(item)
    return {'products':result}


def validate_product(c, body, store, user):
    if store['status']!='active':
        fail('store_pending',403)
    if not c.execute('SELECT 1 FROM categories WHERE id=?',(body.category_id,)).fetchone():
        fail('invalid_input',422)
    if body.media_id and not c.execute('SELECT 1 FROM media WHERE id=? AND owner_id=?',(body.media_id,user['id'])).fetchone():
        fail('forbidden',403)


@router.post('/products', status_code=201)
def create_product(body: schemas.ProductInput,request: Request,user=Depends(seller_user)):
    throttle(request,'product_create',30,3600,str(user['id']))
    try:
        with request.app.state.db.transaction() as c:
            store = own_store(c,user)
            validate_product(c,body,store,user)
            if c.execute('SELECT COUNT(*) FROM products WHERE store_id=?',(store['id'],)).fetchone()[0]>=10:
                fail('seller_limit',409)
            data = body.model_dump(exclude={'expected_version'})
            product_id = insert_id(c, 'INSERT INTO products(store_id,'+','.join(data)+',created_at,updated_at) VALUES(?,'+','.join('?' for _ in data)+',?,?)',
                                   [store['id'],*data.values(),now(),now()])
            c.execute('INSERT INTO inventory_movements(product_id,actor_id,delta,reason,created_at) VALUES(?,?,?,?,?)',
                      (product_id,user['id'],body.stock,'initial',now()))
            if body.media_id:
                c.execute('INSERT INTO product_images(product_id,media_id,position,created_at) VALUES(?,?,0,?)',
                          (product_id,body.media_id,now()))
            audit(c,user['id'],'product_created','product',product_id)
            r = c.execute(PRODUCT_SQL+' WHERE p.id=?',(product_id,)).fetchone()
    except DatabaseIntegrityError:
        fail('sku_exists',409)
    return {'product':product_dict(r,True)}


@router.put('/products/{product_id}')
def edit_product(product_id: int,body: schemas.ProductInput,request: Request,user=Depends(seller_user)):
    try:
        with request.app.state.db.transaction() as c:
            store = own_store(c,user)
            r = c.execute('SELECT * FROM products WHERE id=? AND store_id=?',(product_id,store['id'])).fetchone()
            if not r:
                fail('not_found',404)
            if r['version'] != body.expected_version:
                fail('invalid_status',409)
            validate_product(c,body,store,user)
            has_variants = c.execute(
                "SELECT 1 FROM product_variants WHERE product_id=? AND active=TRUE LIMIT 1",
                (product_id,),
            ).fetchone()
            if has_variants and (body.stock != r['stock'] or body.price_fils != r['price_fils']):
                fail('invalid_input',422)
            data = body.model_dump(exclude={'expected_version'})
            c.execute('UPDATE products SET '+','.join(k+'=?' for k in data)+',updated_at=?,version=version+1,review_note=\'\' WHERE id=?',
                      [*data.values(),now(),product_id])
            if body.media_id != r['media_id']:
                c.execute('DELETE FROM product_images WHERE product_id=?',(product_id,))
                if body.media_id:
                    c.execute('INSERT INTO product_images(product_id,media_id,position,created_at) VALUES(?,?,0,?)',
                              (product_id,body.media_id,now()))
            if body.stock != r['stock']:
                c.execute('INSERT INTO inventory_movements(product_id,actor_id,delta,reason,created_at) VALUES(?,?,?,?,?)',
                          (product_id,user['id'],body.stock-r['stock'],'adjustment',now()))
            audit(c,user['id'],'product_updated','product',product_id)
            result = c.execute(PRODUCT_SQL+' WHERE p.id=?',(product_id,)).fetchone()
    except DatabaseIntegrityError:
        fail('sku_exists',409)
    return {'product':product_dict(result,True)}



@router.get('/products/{product_id}/images')
def get_product_images(product_id: int,request: Request,user=Depends(seller_user)):
    with request.app.state.db.read() as c:
        store = own_store(c,user)
        product = c.execute('SELECT id,media_id FROM products WHERE id=? AND store_id=?',
                            (product_id,store['id'])).fetchone()
        if not product:
            fail('not_found',404)
        rows = c.execute('''SELECT pi.media_id,pi.position FROM product_images pi
          JOIN media m ON m.id=pi.media_id WHERE pi.product_id=? ORDER BY pi.position,pi.id''',
                         (product_id,)).fetchall()
        if not rows and product['media_id']:
            rows = [{'media_id':product['media_id'],'position':0}]
    images = [{'media_id':r['media_id'],'position':r['position'],
               'url':f"/media/{r['media_id']}/480.webp"} for r in rows]
    return {'images':images,'media_ids':[r['media_id'] for r in rows]}


@router.put('/products/{product_id}/images')
def set_product_images(product_id: int,body: schemas.ProductImagesInput,
                       request: Request,user=Depends(seller_user)):
    with request.app.state.db.transaction() as c:
        store = own_store(c,user)
        product = c.execute('SELECT * FROM products WHERE id=? AND store_id=?',
                            (product_id,store['id'])).fetchone()
        if not product:
            fail('not_found',404)
        if product['version'] != body.expected_version:
            fail('invalid_status',409)
        placeholders = ','.join('?' for _ in body.media_ids)
        owned = c.execute(
            f'SELECT id FROM media WHERE owner_id=? AND id IN ({placeholders})',
            [user['id'],*body.media_ids],
        ).fetchall()
        if len(owned) != len(body.media_ids):
            fail('forbidden',403)
        c.execute('DELETE FROM product_images WHERE product_id=?',(product_id,))
        stamp = now()
        for position,media_id in enumerate(body.media_ids):
            c.execute('INSERT INTO product_images(product_id,media_id,position,created_at) VALUES(?,?,?,?)',
                      (product_id,media_id,position,stamp))
        changed = c.execute('''UPDATE products SET media_id=?,status=CASE WHEN status='active' THEN 'pending' ELSE status END,
          review_note='',updated_at=?,version=version+1 WHERE id=? AND version=?''',
                                (body.media_ids[0],stamp,product_id,body.expected_version))
        if changed.rowcount != 1:
            fail('invalid_status',409)
        audit(c,user['id'],'product_gallery_updated','product',product_id)
    return {'ok':True,'media_ids':body.media_ids,'version':body.expected_version + 1}



def variant_dict(row, private=False):
    result = {
        'id':row['id'],
        'attributes':json.loads(row['attributes_json']),
        'price_fils':row['price_fils'],
        'compare_at_fils':row['compare_at_fils'],
        'stock':row['stock'],
        'active':bool(row['active']),
    }
    if private:
        result.update(sku=row['sku'],version=row['version'])
    return result


@router.get('/products/{product_id}/variants')
def get_product_variants(product_id: int,request: Request,user=Depends(seller_user)):
    with request.app.state.db.read() as c:
        store = own_store(c,user)
        product = c.execute('SELECT id FROM products WHERE id=? AND store_id=?',
                            (product_id,store['id'])).fetchone()
        if not product:
            fail('not_found',404)
        rows = c.execute('SELECT * FROM product_variants WHERE product_id=? ORDER BY id',
                         (product_id,)).fetchall()
    return {'variants':[variant_dict(row,True) for row in rows]}


@router.put('/products/{product_id}/variants')
def set_product_variants(product_id: int,body: schemas.ProductVariantsInput,
                         request: Request,user=Depends(seller_user)):
    try:
        with request.app.state.db.transaction() as c:
            store = own_store(c,user)
            product = c.execute('SELECT * FROM products WHERE id=? AND store_id=?',
                                (product_id,store['id'])).fetchone()
            if not product:
                fail('not_found',404)
            if product['version'] != body.expected_version:
                fail('invalid_status',409)
            existing = c.execute('SELECT * FROM product_variants WHERE product_id=?',
                                 (product_id,)).fetchall()
            existing_by_id = {row['id']:row for row in existing}
            requested_ids = {item.id for item in body.variants if item.id is not None}
            if any(identifier not in existing_by_id for identifier in requested_ids):
                fail('forbidden',403)

            stamp = now()
            total_stock = sum(item.stock for item in body.variants)
            if total_stock > 1000000:
                fail('invalid_input',422)
            saved_ids = set()
            for item in body.variants:
                attributes_json = json.dumps(item.attributes,sort_keys=True,ensure_ascii=False,separators=(',',':'))
                collision = c.execute(
                    '''SELECT 1 FROM products WHERE store_id=? AND sku=?
                       UNION ALL
                       SELECT 1 FROM product_variants WHERE store_id=? AND sku=? AND product_id<>? LIMIT 1''',
                    (store['id'],item.sku,store['id'],item.sku,product_id),
                ).fetchone()
                if collision:
                    fail('sku_exists',409)
                if item.id is None:
                    variant_id = insert_id(c,'''INSERT INTO product_variants(
                      product_id,store_id,sku,attributes_json,price_fils,compare_at_fils,stock,active,version,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,TRUE,1,?,?)''',
                    (product_id,store['id'],item.sku,attributes_json,item.price_fils,
                     item.compare_at_fils,item.stock,stamp,stamp))
                    delta = item.stock
                    reason = f'variant_initial_{variant_id}'
                else:
                    old = existing_by_id[item.id]
                    variant_id = item.id
                    delta = item.stock - old['stock']
                    reason = f'variant_adjust_{variant_id}_{old["version"]+1}'
                    c.execute('''UPDATE product_variants SET sku=?,attributes_json=?,price_fils=?,
                      compare_at_fils=?,stock=?,active=TRUE,version=version+1,updated_at=?
                      WHERE id=? AND product_id=?''',
                      (item.sku,attributes_json,item.price_fils,item.compare_at_fils,
                       item.stock,stamp,variant_id,product_id))
                if delta:
                    c.execute('''INSERT INTO inventory_movements(product_id,actor_id,delta,reason,created_at)
                      VALUES(?,?,?,?,?)''',(product_id,user['id'],delta,reason,stamp))
                saved_ids.add(variant_id)

            if not existing and product['stock']:
                c.execute('''INSERT INTO inventory_movements(product_id,actor_id,delta,reason,created_at)
                  VALUES(?,?,?,?,?)''',(product_id,user['id'],-product['stock'],'variant_mode_transfer',stamp))
            for old in existing:
                if old['id'] not in saved_ids and old['active']:
                    c.execute('UPDATE product_variants SET active=FALSE,version=version+1,updated_at=? WHERE id=?',
                              (stamp,old['id']))
                    if old['stock']:
                        c.execute('''INSERT INTO inventory_movements(product_id,actor_id,delta,reason,created_at)
                          VALUES(?,?,?,?,?)''',(product_id,user['id'],-old['stock'],f'variant_disabled_{old["id"]}_{old["version"]+1}',stamp))
            low_price = min(item.price_fils for item in body.variants)
            changed = c.execute('''UPDATE products SET stock=?,price_fils=?,compare_at_fils=NULL,
              status=CASE WHEN status='active' THEN 'pending' ELSE status END,
              review_note='',version=version+1,updated_at=? WHERE id=? AND version=?''',
                                (total_stock,low_price,stamp,product_id,body.expected_version))
            if changed.rowcount != 1:
                fail('invalid_status',409)
            audit(c,user['id'],'product_variants_updated','product',product_id,
                  f'{len(body.variants)} active variants')
            result = c.execute('SELECT version FROM products WHERE id=?',(product_id,)).fetchone()
            saved_rows = c.execute('SELECT * FROM product_variants WHERE product_id=? ORDER BY id',
                                   (product_id,)).fetchall()
    except DatabaseIntegrityError:
        fail('sku_exists',409)
    return {'ok':True,'version':result['version'],
            'variants':[variant_dict(row,True) for row in saved_rows]}


@router.patch('/products/{product_id}/stock')
def update_stock(product_id: int,body: schemas.StockInput,request: Request,user=Depends(seller_user)):
    with request.app.state.db.transaction() as c:
        store = own_store(c,user)
        r = c.execute('SELECT * FROM products WHERE id=? AND store_id=?',(product_id,store['id'])).fetchone()
        if not r:
            fail('not_found',404)
        if c.execute("SELECT 1 FROM product_variants WHERE product_id=? AND active=TRUE LIMIT 1",(product_id,)).fetchone():
            fail('invalid_input',422)
        if r['version'] != body.expected_version:
            fail('invalid_status',409)
        c.execute('UPDATE products SET stock=?,updated_at=?,version=version+1 WHERE id=?',(body.stock,now(),product_id))
        c.execute('INSERT INTO inventory_movements(product_id,actor_id,delta,reason,created_at) VALUES(?,?,?,?,?)',
                  (product_id,user['id'],body.stock-r['stock'],'adjustment',now()))
        audit(c,user['id'],'stock_adjusted','product',product_id)
    return {'ok':True}


@router.get('/inventory')
def inventory(request: Request,user=Depends(seller_user)):
    with request.app.state.db.read() as c:
        store = own_store(c,user)
        rows = c.execute('SELECT im.* FROM inventory_movements im JOIN products p ON p.id=im.product_id WHERE p.store_id=? ORDER BY im.id DESC LIMIT 200',(store['id'],)).fetchall()
    return {'movements':[dict(r) for r in rows]}


@router.get('/summary')
def summary(request: Request,user=Depends(seller_user)):
    with request.app.state.db.read() as c:
        store = own_store(c,user)
        row = c.execute('''SELECT COUNT(*) AS orders,
          COALESCE(SUM(CASE WHEN status='delivered' THEN subtotal_fils ELSE 0 END),0) AS delivered_sales_fils,
          COALESCE(SUM(CASE WHEN status='delivered' THEN commission_fils ELSE 0 END),0) AS commission_due_fils,
          COALESCE(SUM(CASE WHEN status='delivered' THEN net_fils ELSE 0 END),0) AS net_sales_fils
          FROM seller_orders WHERE store_id=?''',(store['id'],)).fetchone()
        low = c.execute('SELECT id,title_ar,title_en,stock FROM products WHERE store_id=? AND stock<=3',(store['id'],)).fetchall()
    return {'summary':dict(row),'low_stock':[dict(r) for r in low]}


@router.post('/media', status_code=201)
def upload_image(request: Request,file: UploadFile=File(...),user=Depends(seller_user)):
    throttle(request,'media',30,3600,str(user['id']))
    data = file.file.read(5*1024*1024+1)
    if len(data)>5*1024*1024:
        fail('media_size',413)
    try:
        with IMAGE_JOBS, warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            im = Image.open(BytesIO(data))
            if im.format not in ('JPEG','PNG','WEBP'):
                fail('media_type',415)
            if im.width*im.height>16_000_000:
                fail('media_size',413)
            im.load()
            im = ImageOps.exif_transpose(im).convert('RGB')
            images = {}
            for size in (960,480):
                copy = im.copy()
                copy.thumbnail((size,size))
                out = BytesIO()
                copy.save(out,format='WEBP',quality=85,method=4)
                images[size] = out.getvalue()
    except (Image.DecompressionBombWarning,Image.DecompressionBombError):
        fail('media_size',413)
    except (UnidentifiedImageError,OSError,ValueError):
        fail('media_type',415)
    image_digest = hashlib.sha256(images[960]).hexdigest()
    written = []
    try:
        with request.app.state.db.transaction() as c:
            own_store(c,user)
            existing = c.execute('SELECT id FROM media WHERE owner_id=? AND digest=?',(user['id'],image_digest)).fetchone()
            if existing:
                return {'id':existing['id'],'url':f"/media/{existing['id']}/480.webp",'duplicate':True}
            quota = c.execute('SELECT COUNT(*),COALESCE(SUM(bytes),0) FROM media WHERE owner_id=?',(user['id'],)).fetchone()
            size_bytes = sum(map(len,images.values()))
            if quota[0]>=50 or quota[1]+size_bytes>50*1024*1024:
                fail('media_quota',413)
            key = secrets.token_hex(16)
            folder = request.app.state.settings.data_dir / 'media'
            folder.mkdir(exist_ok=True,mode=0o700)
            for size,content in images.items():
                p = folder/f'{key}-{size}.webp'
                fd = os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
                with os.fdopen(fd,'wb') as f:
                    f.write(content)
                written.append(p)
            media_id = insert_id(c, 'INSERT INTO media(owner_id,digest,storage_key,bytes,created_at) VALUES(?,?,?,?,?)',
                                 (user['id'],image_digest,key,size_bytes,now()))
    except BaseException:
        for p in written:
            p.unlink(missing_ok=True)
        raise
    return {'id':media_id,'url':f'/media/{media_id}/480.webp','duplicate':False}
