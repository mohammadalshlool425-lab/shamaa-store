import re
import secrets
import time
from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request
from fastapi.responses import JSONResponse
from ..commerce import (PRODUCT_SQL, PUBLIC_WHERE, canonical_hash, cart_quote, commission, commission_rate)
from ..database import now
from ..errors import fail
from .. import mail, schemas
from ..security import audit, require_user, seller_user, throttle, verified_user
from .seller import own_store

router = APIRouter(prefix='/api', tags=['Orders'])
NEXT = {'new':{'confirmed','cancelled'},'confirmed':{'preparing','cancelled'},
        'preparing':{'shipped','cancelled'},'shipped':{'delivered'},'delivered':set(),'cancelled':set()}


def shipment_dict(c, row):
    d = dict(row)
    d['items'] = [dict(r) for r in c.execute('SELECT product_id,title_ar,title_en,unit_price_fils,quantity,image_url,commission_bps,commission_fils FROM order_items WHERE seller_order_id=?',(row['id'],))]
    d['events'] = [dict(r) for r in c.execute('SELECT from_status,to_status,note,created_at FROM order_events WHERE seller_order_id=? ORDER BY id',(row['id'],))]
    s = c.execute('SELECT name_ar,name_en,slug FROM stores WHERE id=?',(row['store_id'],)).fetchone()
    d['store_name_ar'], d['store_name_en'], d['store_slug'] = s['name_ar'],s['name_en'],s['slug']
    return d


def order_dict(c, row):
    d = {k:row[k] for k in ('id','number','recipient','phone','city','address','subtotal_fils','shipping_fils','total_fils','payment_method','is_demo','created_at')}
    d['shipments'] = [shipment_dict(c,r) for r in c.execute('SELECT * FROM seller_orders WHERE order_id=? ORDER BY id',(row['id'],))]
    d['live_total_fils'] = sum(s['subtotal_fils']+s['shipping_fils'] for s in d['shipments'] if s['status']!='cancelled')
    return d


@router.get('/cart')
def get_cart(request: Request,user=Depends(require_user)):
    with request.app.state.db.read() as c:
        return cart_quote(c,user['id'])


@router.put('/cart')
def set_cart(body: schemas.CartItem,request: Request,user=Depends(require_user)):
    with request.app.state.db.transaction() as c:
        if body.quantity==0:
            c.execute('DELETE FROM carts WHERE user_id=? AND product_id=?',(user['id'],body.product_id))
        else:
            p = c.execute(PRODUCT_SQL+' WHERE p.id=? AND '+PUBLIC_WHERE,(body.product_id,)).fetchone()
            if not p:
                fail('not_found',404)
            if p['owner_id']==user['id']:
                fail('own_product',409)
            if p['stock']<body.quantity:
                fail('stock',409)
            if c.execute('SELECT COUNT(*) FROM carts WHERE user_id=?',(user['id'],)).fetchone()[0]>=50 and not c.execute('SELECT 1 FROM carts WHERE user_id=? AND product_id=?',(user['id'],body.product_id)).fetchone():
                fail('invalid_input',422)
            c.execute('INSERT INTO carts VALUES(?,?,?) ON CONFLICT(user_id,product_id) DO UPDATE SET quantity=excluded.quantity',
                      (user['id'],body.product_id,body.quantity))
        return cart_quote(c,user['id'])


@router.post('/orders')
def checkout(body: schemas.Checkout,request: Request,tasks: BackgroundTasks,
             idempotency_key: str=Header(default='',alias='Idempotency-Key'),user=Depends(verified_user)):
    if not re.fullmatch(r'[A-Za-z0-9_-]{16,100}',idempotency_key):
        fail('invalid_input',422)
    throttle(request,'checkout',30,3600,str(user['id']))
    request_hash = canonical_hash(body.model_dump())
    settings = request.app.state.settings
    with request.app.state.db.transaction() as c:
        previous = c.execute('SELECT * FROM orders WHERE buyer_id=? AND idempotency_key=?',(user['id'],idempotency_key)).fetchone()
        if previous:
            if previous['request_hash']!=request_hash:
                fail('idempotency',409)
            return JSONResponse({'order':order_dict(c,previous),'replayed':True})
        quote = cart_quote(c,user['id'],strict=True)
        if not quote['groups']:
            fail('cart_empty',409)
        if quote['total_fils']!=body.expected_total_fils or quote['quote_hash']!=body.quote_hash:
            fail('cart_changed',409)
        is_demo = not settings.production or any(g['is_demo'] for g in quote['groups'])
        if is_demo and settings.production:
            fail('demo_disabled',403)
        cur = c.execute('''INSERT INTO orders(number,buyer_id,idempotency_key,request_hash,recipient,phone,city,address,
          subtotal_fils,shipping_fils,total_fils,is_demo,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
          ('pending-'+secrets.token_hex(12),user['id'],idempotency_key,request_hash,body.recipient,body.phone,body.city,body.address,
           quote['subtotal_fils'],quote['shipping_fils'],quote['total_fils'],int(is_demo),now()))
        order_id = cur.lastrowid
        number = f'SH-{time.strftime("%Y",time.gmtime())}-{order_id:07}'
        c.execute('UPDATE orders SET number=? WHERE id=?',(number,order_id))
        for group in quote['groups']:
            items = []
            for item in group['items']:
                p = c.execute(PRODUCT_SQL+' WHERE p.id=?',(item['id'],)).fetchone()
                bps = commission_rate(c,p)
                amount = commission(item['line_total_fils'],bps)
                items.append((item,bps,amount))
            commission_fils = sum(amount for _,_,amount in items)
            sub = c.execute('''INSERT INTO seller_orders(order_id,store_id,subtotal_fils,shipping_fils,commission_fils,net_fils,updated_at)
              VALUES(?,?,?,?,?,?,?)''',(order_id,group['store_id'],group['subtotal_fils'],group['shipping_fils'],commission_fils,
              group['subtotal_fils']+group['shipping_fils']-commission_fils,now())).lastrowid
            for item,bps,amount in items:
                result = c.execute("UPDATE products SET stock=stock-?,updated_at=?,version=version+1 WHERE id=? AND stock>=? AND status='active'",
                                   (item['quantity'],now(),item['id'],item['quantity']))
                if result.rowcount!=1:
                    fail('stock',409)
                c.execute('''INSERT INTO order_items(seller_order_id,product_id,title_ar,title_en,unit_price_fils,quantity,commission_bps,commission_fils,image_url)
                  VALUES(?,?,?,?,?,?,?,?,?)''',(sub,item['id'],item['title_ar'],item['title_en'],item['price_fils'],item['quantity'],bps,amount,item['image_url']))
                c.execute('INSERT INTO inventory_movements(product_id,actor_id,delta,reason,seller_order_id,created_at) VALUES(?,?,?,?,?,?)',
                          (item['id'],user['id'],-item['quantity'],'sale',sub,now()))
            c.execute('INSERT INTO order_events(seller_order_id,actor_id,to_status,created_at) VALUES(?,?,?,?)',(sub,user['id'],'new',now()))
            owner_id = c.execute('SELECT owner_id FROM stores WHERE id=?',(group['store_id'],)).fetchone()[0]
            mail.notify(c,settings,owner_id,'order',f'طلب جديد {number} في متجرك. راجع لوحة الطلبات.',f'New order {number}. Review it in your seller dashboard.')
        c.execute('DELETE FROM carts WHERE user_id=?',(user['id'],))
        mail.notify(c,settings,user['id'],'order',f'حُفظ طلبك {number}. الدفع عند الاستلام، وحالة كل شحنة متاحة في حسابك.',
                    f'Order {number} was saved. Payment is cash on delivery. Track each shipment in your account.')
        audit(c,user['id'],'order_created','order',order_id)
        result = order_dict(c,c.execute('SELECT * FROM orders WHERE id=?',(order_id,)).fetchone())
    tasks.add_task(mail.deliver_pending,request.app.state.db,settings)
    return JSONResponse({'order':result,'replayed':False},status_code=201)


@router.get('/orders')
def my_orders(request: Request,user=Depends(require_user)):
    with request.app.state.db.read() as c:
        rows = c.execute('SELECT * FROM orders WHERE buyer_id=? ORDER BY id DESC LIMIT 50',(user['id'],)).fetchall()
        return {'orders':[order_dict(c,r) for r in rows]}


@router.get('/order-attempts/{key}')
def recover_order(key: str,request: Request,user=Depends(require_user)):
    if len(key)>100:
        fail('not_found',404)
    with request.app.state.db.read() as c:
        row = c.execute('SELECT * FROM orders WHERE buyer_id=? AND idempotency_key=?',(user['id'],key)).fetchone()
        if not row:
            fail('not_found',404)
        return {'order':order_dict(c,row)}


@router.get('/orders/{order_id}')
def my_order(order_id: int,request: Request,user=Depends(require_user)):
    with request.app.state.db.read() as c:
        row = c.execute('SELECT * FROM orders WHERE id=? AND buyer_id=?',(order_id,user['id'])).fetchone()
        if not row:
            fail('not_found',404)
        return {'order':order_dict(c,row)}


def change_status(c,request,user,sub,status,expected,note,buyer=False):
    old = sub['status']
    if old!=expected or status not in NEXT[old] or (buyer and not (old in ('new','confirmed') and status=='cancelled')):
        fail('invalid_status',409)
    if status=='cancelled':
        for item in c.execute('SELECT product_id,quantity FROM order_items WHERE seller_order_id=?',(sub['id'],)).fetchall():
            c.execute('UPDATE products SET stock=stock+?,updated_at=?,version=version+1 WHERE id=?',(item['quantity'],now(),item['product_id']))
            c.execute('INSERT INTO inventory_movements(product_id,actor_id,delta,reason,seller_order_id,created_at) VALUES(?,?,?,?,?,?)',
                      (item['product_id'],user['id'],item['quantity'],'cancel',sub['id'],now()))
    if status=='delivered':
        # COD: a commission debt is accrued. This is NOT a payment or a seller payout.
        c.execute("INSERT INTO ledger(seller_order_id,store_id,kind,amount_fils,created_at) VALUES(?,?,'cod_commission_due',?,?)",
                  (sub['id'],sub['store_id'],sub['commission_fils'],now()))
    c.execute('UPDATE seller_orders SET status=?,updated_at=? WHERE id=?',(status,now(),sub['id']))
    c.execute('INSERT INTO order_events(seller_order_id,actor_id,from_status,to_status,note,created_at) VALUES(?,?,?,?,?,?)',
              (sub['id'],user['id'],old,status,note,now()))
    audit(c,user['id'],'order_status','seller_order',sub['id'],f'{old}->{status}')
    order = c.execute('SELECT buyer_id,number FROM orders WHERE id=?',(sub['order_id'],)).fetchone()
    owner_id = c.execute('SELECT owner_id FROM stores WHERE id=?',(sub['store_id'],)).fetchone()[0]
    for uid in {order['buyer_id'],owner_id}:
        mail.notify(c,request.app.state.settings,uid,'order_status',f'تحديث على الطلب {order["number"]}. راجع سجل الشحنة في حسابك.',
                    f'Order {order["number"]} has an update. Review its shipment history in your account.')


@router.post('/orders/{order_id}/shipments/{shipment_id}/cancel')
def cancel_shipment(order_id: int,shipment_id: int,request: Request,tasks: BackgroundTasks,user=Depends(require_user)):
    with request.app.state.db.transaction() as c:
        order = c.execute('SELECT * FROM orders WHERE id=? AND buyer_id=?',(order_id,user['id'])).fetchone()
        if not order:
            fail('not_found',404)
        sub = c.execute('SELECT * FROM seller_orders WHERE id=? AND order_id=?',(shipment_id,order_id)).fetchone()
        if not sub:
            fail('not_found',404)
        change_status(c,request,user,sub,'cancelled',sub['status'],'Cancelled by buyer',True)
        result = order_dict(c,order)
    tasks.add_task(mail.deliver_pending,request.app.state.db,request.app.state.settings)
    return {'order':result}


@router.get('/seller/orders')
def seller_orders(request: Request,user=Depends(seller_user)):
    with request.app.state.db.read() as c:
        store = own_store(c,user)
        rows = c.execute('SELECT * FROM seller_orders WHERE store_id=? ORDER BY id DESC LIMIT 100',(store['id'],)).fetchall()
        result = []
        for row in rows:
            d = shipment_dict(c,row)
            # Only this seller's shipment and the delivery details needed to fulfil it.
            o = c.execute('SELECT number,recipient,phone,city,address,created_at,is_demo FROM orders WHERE id=?',(row['order_id'],)).fetchone()
            d['order'] = dict(o)
            result.append(d)
    return {'orders':result}


@router.patch('/seller/orders/{shipment_id}/status')
def seller_status(shipment_id: int,body: schemas.StatusChange,request: Request,tasks: BackgroundTasks,user=Depends(seller_user)):
    with request.app.state.db.transaction() as c:
        store = own_store(c,user)
        sub = c.execute('SELECT * FROM seller_orders WHERE id=? AND store_id=?',(shipment_id,store['id'])).fetchone()
        if not sub:
            fail('not_found',404)
        change_status(c,request,user,sub,body.status,body.expected_status,body.note)
    tasks.add_task(mail.deliver_pending,request.app.state.db,request.app.state.settings)
    return {'ok':True}
