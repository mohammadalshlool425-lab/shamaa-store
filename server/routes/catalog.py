from typing import Literal
from fastapi import APIRouter, Query, Request, Depends
from ..commerce import CITIES, PRODUCT_SQL, PUBLIC_WHERE, product_dict, store_dict
from ..errors import fail
from ..security import require_user

router = APIRouter(prefix='/api', tags=['Catalog'])


@router.get('/bootstrap')
def bootstrap(request: Request):
    with request.app.state.db.read() as c:
        categories = [dict(r) for r in c.execute('''SELECT c.*,(SELECT COUNT(*) FROM products p JOIN stores s ON s.id=p.store_id
          JOIN users u ON u.id=s.owner_id WHERE p.status='active' AND s.status='active' AND u.active=1
          AND (p.category_id=c.id OR p.category_id IN (SELECT id FROM categories WHERE parent_id=c.id))) AS product_count FROM categories c''')]
        content = dict(c.execute('SELECT key,value FROM settings').fetchall())
    s = request.app.state.settings
    for cat in categories:
        cat.pop('commission_bps',None)
    return {'brand':"SHAM’A",'currency':'JOD','countries':['JO'],
            'categories':categories,'cities':[{'id':i,'name_ar':a,'name_en':e} for i,a,e in CITIES],
            'content':{k:v for k,v in content.items() if k.startswith('home_')},
            'development':not s.production,'demo':s.demo,'email_mode':s.email_mode,
            'features':{'cod':True,'digital':False,'used':False,'ai':False,'online_payment':False,'payouts':False}}


@router.get('/products')
def products(request: Request,q: str=Query('',max_length=150),category: str=Query('',max_length=50),
             city: str=Query('',max_length=30),store: str=Query('',max_length=50),
             min_price: int=Query(0,ge=0), max_price: int=Query(1000000000,ge=0),
             sort: Literal['recent','price_asc','price_desc','featured','bestselling']='featured',
             offers: bool=False,page: int=Query(1,ge=1,le=10000),limit: int=Query(12,ge=1,le=48)):
    where, params = [PUBLIC_WHERE,'p.price_fils BETWEEN ? AND ?'], [min_price,max_price]
    if q.strip():
        # Parameters bind all user text; LIKE wildcards remain harmless to SQL.
        where.append('(p.title_ar LIKE ? OR p.title_en LIKE ? OR p.description_ar LIKE ? OR p.description_en LIKE ?)')
        params += [f'%{q.strip()}%']*4
    if category:
        where.append('(p.category_id=? OR c.parent_id=?)')
        params += [category,category]
    if city:
        where.append('s.city=?'); params.append(city)
    if store:
        where.append('s.slug=?'); params.append(store)
    if offers:
        where.append('p.compare_at_fils IS NOT NULL')
    order = {'recent':'p.created_at DESC,p.id DESC','featured':'p.featured DESC,p.id ASC',
             'price_asc':'p.price_fils ASC,p.id','price_desc':'p.price_fils DESC,p.id',
             'bestselling':"(SELECT COALESCE(SUM(oi.quantity),0) FROM order_items oi JOIN seller_orders so ON so.id=oi.seller_order_id WHERE oi.product_id=p.id AND so.status='delivered') DESC,p.id"}[sort]
    clause = ' WHERE ' + ' AND '.join(where)
    with request.app.state.db.read() as c:
        total = c.execute('SELECT COUNT(*) FROM ('+PRODUCT_SQL+clause+')',params).fetchone()[0]
        rows = c.execute(PRODUCT_SQL+clause+' ORDER BY '+order+' LIMIT ? OFFSET ?',params+[limit,(page-1)*limit]).fetchall()
    return {'products':[product_dict(r) for r in rows],'total':total,'page':page,'pages':(total+limit-1)//limit}


@router.get('/products/{product_id}')
def product(product_id: int,request: Request):
    with request.app.state.db.read() as c:
        row = c.execute(PRODUCT_SQL+' WHERE p.id=? AND '+PUBLIC_WHERE,(product_id,)).fetchone()
        if not row:
            fail('not_found',404)
        similar = c.execute(PRODUCT_SQL+' WHERE p.id<>? AND (p.category_id=? OR c.parent_id=?) AND '+PUBLIC_WHERE+' LIMIT 4',
                            (product_id,row['category_id'],row['parent_id'])).fetchall()
    return {'product':product_dict(row),'similar':[product_dict(r) for r in similar]}


@router.get('/stores')
def stores(request: Request):
    with request.app.state.db.read() as c:
        rows = c.execute("SELECT s.*,(SELECT COUNT(*) FROM products p WHERE p.store_id=s.id AND p.status='active') AS product_count FROM stores s JOIN users u ON u.id=s.owner_id WHERE s.status='active' AND u.active=1 ORDER BY s.id LIMIT 60").fetchall()
    return {'stores':[{**store_dict(r),'product_count':r['product_count']} for r in rows]}


@router.get('/stores/{slug}')
def store(slug: str,request: Request):
    with request.app.state.db.read() as c:
        r = c.execute("SELECT s.* FROM stores s JOIN users u ON u.id=s.owner_id WHERE s.slug=? AND s.status='active' AND u.active=1",(slug,)).fetchone()
        if not r:
            fail('not_found',404)
    return {'store':store_dict(r)}


@router.get('/notifications')
def notifications(request: Request,user=Depends(require_user)):
    with request.app.state.db.read() as c:
        rows = c.execute('SELECT id,kind,body_ar,body_en,read_at,created_at FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 60',(user['id'],)).fetchall()
    return {'notifications':[dict(r) for r in rows]}


@router.post('/notifications/read')
def read_notifications(request: Request,user=Depends(require_user)):
    from ..database import now
    with request.app.state.db.transaction() as c:
        c.execute('UPDATE notifications SET read_at=? WHERE user_id=? AND read_at IS NULL',(now(),user['id']))
    return {'ok':True}
