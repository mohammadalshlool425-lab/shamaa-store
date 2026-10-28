import hashlib
from io import BytesIO
import os
import secrets
import sqlite3
import warnings
from threading import BoundedSemaphore
from fastapi import APIRouter, BackgroundTasks, Depends, File, Request, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from ..commerce import PRODUCT_SQL, product_dict, store_dict
from ..database import now
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
            cur = c.execute(f'INSERT INTO stores(owner_id,{keys},created_at) VALUES(?,{marks},?)',
                            [user['id'],*data.values(),now()])
            c.execute("UPDATE users SET role='seller' WHERE id=?",(user['id'],))
            audit(c,user['id'],'store_requested','store',cur.lastrowid)
            row = c.execute('SELECT * FROM stores WHERE id=?',(cur.lastrowid,)).fetchone()
    except sqlite3.IntegrityError:
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
    except sqlite3.IntegrityError:
        fail('store_exists',409)
    return {'store':store_dict(row,True)}


@router.get('/products')
def my_products(request: Request,user=Depends(seller_user)):
    with request.app.state.db.read() as c:
        store = own_store(c,user)
        rows = c.execute(PRODUCT_SQL+' WHERE p.store_id=? ORDER BY p.id DESC',(store['id'],)).fetchall()
    return {'products':[product_dict(r,True) for r in rows]}


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
            cur = c.execute('INSERT INTO products(store_id,'+','.join(data)+',created_at,updated_at) VALUES(?,'+','.join('?' for _ in data)+',?,?)',
                            [store['id'],*data.values(),now(),now()])
            product_id = cur.lastrowid
            c.execute('INSERT INTO inventory_movements(product_id,actor_id,delta,reason,created_at) VALUES(?,?,?,?,?)',
                      (product_id,user['id'],body.stock,'initial',now()))
            audit(c,user['id'],'product_created','product',product_id)
            r = c.execute(PRODUCT_SQL+' WHERE p.id=?',(product_id,)).fetchone()
    except sqlite3.IntegrityError:
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
            data = body.model_dump(exclude={'expected_version'})
            c.execute('UPDATE products SET '+','.join(k+'=?' for k in data)+',updated_at=?,version=version+1,review_note=\'\' WHERE id=?',
                      [*data.values(),now(),product_id])
            if body.stock != r['stock']:
                c.execute('INSERT INTO inventory_movements(product_id,actor_id,delta,reason,created_at) VALUES(?,?,?,?,?)',
                          (product_id,user['id'],body.stock-r['stock'],'adjustment',now()))
            audit(c,user['id'],'product_updated','product',product_id)
            result = c.execute(PRODUCT_SQL+' WHERE p.id=?',(product_id,)).fetchone()
    except sqlite3.IntegrityError:
        fail('sku_exists',409)
    return {'product':product_dict(result,True)}


@router.patch('/products/{product_id}/stock')
def update_stock(product_id: int,body: schemas.StockInput,request: Request,user=Depends(seller_user)):
    with request.app.state.db.transaction() as c:
        store = own_store(c,user)
        r = c.execute('SELECT * FROM products WHERE id=? AND store_id=?',(product_id,store['id'])).fetchone()
        if not r:
            fail('not_found',404)
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
            cur = c.execute('INSERT INTO media(owner_id,digest,storage_key,bytes,created_at) VALUES(?,?,?,?,?)',
                            (user['id'],image_digest,key,size_bytes,now()))
            media_id = cur.lastrowid
    except BaseException:
        for p in written:
            p.unlink(missing_ok=True)
        raise
    return {'id':media_id,'url':f'/media/{media_id}/480.webp','duplicate':False}
