from concurrent.futures import ThreadPoolExecutor
import secrets
import pytest
from server.commerce import commission


def create_order(market):
    f=market['factory'];buyer=market['buyer']
    f.cart(buyer,market['p'],2);quote=f.cart(buyer,market['q'],1)
    response,key,body=f.checkout(buyer,quote)
    assert response.status_code==201,response.text
    return response.json()['order'],key,body


def test_multivendor_atomic_order_money_inventory_and_commission(market):
    f=market['factory'];order,_,_=create_order(market)
    assert len(order['shipments'])==2
    assert order['subtotal_fils']==1555*2+10001
    assert order['shipping_fils']==2500+3500
    assert order['total_fils']==19111
    assert order['payment_method']=='cod'
    with f.db.read() as c:
        assert c.execute('SELECT stock FROM products WHERE id=?',(market['p']['id'],)).fetchone()[0]==6
        assert c.execute('SELECT stock FROM products WHERE id=?',(market['q']['id'],)).fetchone()[0]==4
        assert c.execute('SELECT COUNT(*) FROM carts').fetchone()[0]==0
        assert c.execute("SELECT COUNT(*) FROM inventory_movements WHERE reason='sale'").fetchone()[0]==2
        assert c.execute('SELECT COUNT(*) FROM ledger').fetchone()[0]==0
    for s in order['shipments']:
        assert s['commission_fils']==sum(commission(i['unit_price_fils']*i['quantity'],500) for i in s['items'])
        assert s['net_fils']==s['subtotal_fils']+s['shipping_fils']-s['commission_fils']


def test_retries_return_same_order_after_cart_cleared(market):
    order,key,body=create_order(market);client=market['buyer']['client']
    for _ in range(3):
        r=client.post('/api/orders',json=body,headers={'Idempotency-Key':key})
        assert r.status_code==200 and r.json()['order']['id']==order['id'] and r.json()['replayed']
    body['phone']='0781234567'
    r=client.post('/api/orders',json=body,headers={'Idempotency-Key':key})
    assert r.status_code==409 and r.json()['code']=='idempotency'
    assert client.get('/api/order-attempts/'+key).json()['order']['id']==order['id']


def test_order_key_and_data_not_accessible_to_other_buyer(market):
    order,key,_=create_order(market);other=market['factory'].user()['client']
    assert other.get('/api/orders/'+str(order['id'])).status_code==404
    assert other.get('/api/order-attempts/'+key).status_code==404
    s=order['shipments'][0]
    assert other.post(f'/api/orders/{order["id"]}/shipments/{s["id"]}/cancel').status_code==404
    assert other.get('/api/orders').json()['orders']==[]


def test_seller_only_sees_own_shipment(market):
    order,_,_=create_order(market)
    for seller,product,foreign in [(market['a'],market['p'],market['q']),(market['b'],market['q'],market['p'])]:
        shipments=seller['client'].get('/api/seller/orders').json()['orders']
        assert len(shipments)==1
        assert [p['product_id'] for p in shipments[0]['items']]==[product['id']]
        assert str(foreign['id']) not in [str(p['product_id']) for p in shipments[0]['items']]
        assert 'buyer_id' not in shipments[0]['order']
        assert 'phone' in shipments[0]['order']  # necessary to fulfil just this shipment
    b_sub=next(s for s in order['shipments'] if s['store_id']==market['b']['store_id'])
    r=market['a']['client'].patch(f'/api/seller/orders/{b_sub["id"]}/status',json={'status':'confirmed','expected_status':'new'})
    assert r.status_code==404


@pytest.mark.parametrize('change',['price','shipping','stock'])
def test_stale_quote_rolls_back_everything(market,change):
    f=market['factory'];buyer=market['buyer'];quote=f.cart(buyer,market['p'],2)
    with f.db.transaction() as c:
        if change=='price':c.execute('UPDATE products SET price_fils=price_fils+100 WHERE id=?',(market['p']['id'],))
        if change=='shipping':c.execute('UPDATE stores SET shipping_fils=shipping_fils+500 WHERE id=?',(market['a']['store_id'],))
        if change=='stock':c.execute('UPDATE products SET stock=1 WHERE id=?',(market['p']['id'],))
    r,_,_=f.checkout(buyer,quote)
    assert r.status_code==409
    with f.db.read() as c:
        assert c.execute('SELECT COUNT(*) FROM orders').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM carts').fetchone()[0]==1
        assert c.execute("SELECT COUNT(*) FROM inventory_movements WHERE reason='sale'").fetchone()[0]==0


def test_unavailable_second_store_does_not_consume_first_stock(market):
    f=market['factory'];f.cart(market['buyer'],market['p'],2);quote=f.cart(market['buyer'],market['q'],1)
    with f.db.transaction() as c:c.execute("UPDATE stores SET status='suspended' WHERE id=?",(market['b']['store_id'],))
    r,_,_=f.checkout(market['buyer'],quote);assert r.status_code==409
    with f.db.read() as c:assert c.execute('SELECT stock FROM products WHERE id=?',(market['p']['id'],)).fetchone()[0]==8


def test_last_piece_concurrent_buyers_cannot_oversell(factory):
    seller=factory.seller();p=factory.product(seller,stock=1)
    buyers=[factory.user(),factory.user()]
    quotes=[factory.cart(b,p) for b in buyers]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda args:factory.checkout(*args)[0],zip(buyers,quotes)))
    assert sorted(r.status_code for r in results)==[201,409]
    with factory.db.read() as c:
        assert c.execute('SELECT stock FROM products WHERE id=?',(p['id'],)).fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM orders').fetchone()[0]==1
        assert c.execute("SELECT COUNT(*) FROM inventory_movements WHERE reason='sale'").fetchone()[0]==1


def test_simultaneous_retries_create_exactly_one_order(factory):
    seller=factory.seller();p=factory.product(seller,stock=5);buyer=factory.user()
    quote=factory.cart(buyer,p)
    key=secrets.token_urlsafe(24)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:factory.checkout(buyer,quote,key)[0],range(4)))
    assert sorted(r.status_code for r in results)==[200,200,200,201]
    assert len({r.json()['order']['id'] for r in results})==1
    with factory.db.read() as c:assert c.execute('SELECT stock FROM products WHERE id=?',(p['id'],)).fetchone()[0]==4


def test_cancel_restores_stock_exactly_once_and_only_one_shipment(market):
    order,_,_=create_order(market);client=market['buyer']['client'];s=next(s for s in order['shipments'] if s['store_id']==market['a']['store_id'])
    path=f'/api/orders/{order["id"]}/shipments/{s["id"]}/cancel'
    r=client.post(path);assert r.status_code==200
    assert r.json()['order']['live_total_fils']==10001+3500
    assert client.post(path).status_code==409
    with market['factory'].db.read() as c:
        assert c.execute('SELECT stock FROM products WHERE id=?',(market['p']['id'],)).fetchone()[0]==8
        assert c.execute('SELECT stock FROM products WHERE id=?',(market['q']['id'],)).fetchone()[0]==4
        assert c.execute("SELECT COUNT(*) FROM inventory_movements WHERE reason='cancel'").fetchone()[0]==1


def test_status_machine_delivery_ledger_and_no_postdelivery_cancel(market):
    order,_,_=create_order(market);s=next(s for s in order['shipments'] if s['store_id']==market['a']['store_id']);client=market['a']['client']
    path=f'/api/seller/orders/{s["id"]}/status'
    assert client.patch(path,json={'status':'delivered','expected_status':'new'}).status_code==409
    previous='new'
    for new in ['confirmed','preparing','shipped','delivered']:
        r=client.patch(path,json={'status':new,'expected_status':previous,'note':'Recorded update'})
        assert r.status_code==200,r.text;previous=new
    assert client.patch(path,json={'status':'delivered','expected_status':'shipped'}).status_code==409
    assert client.patch(path,json={'status':'cancelled','expected_status':'delivered'}).status_code==409
    assert market['buyer']['client'].post(f'/api/orders/{order["id"]}/shipments/{s["id"]}/cancel').status_code==409
    with market['factory'].db.read() as c:
        entries=c.execute('SELECT * FROM ledger WHERE seller_order_id=?',(s['id'],)).fetchall()
        assert len(entries)==1 and entries[0]['amount_fils']==s['commission_fils']
        assert entries[0]['kind']=='cod_commission_due'
        assert c.execute('SELECT COUNT(*) FROM order_events WHERE seller_order_id=?',(s['id'],)).fetchone()[0]==5


def test_stale_seller_edit_cannot_overwrite_sold_inventory(market):
    f=market['factory'];f.cart(market['buyer'],market['p'],2)
    r,_,_=f.checkout(market['buyer']);assert r.status_code==201
    r=market['a']['client'].patch('/api/seller/products/'+str(market['p']['id'])+'/stock',json={'stock':100,'expected_version':market['p']['version']})
    assert r.status_code==409
    with f.db.read() as c:assert c.execute('SELECT stock FROM products WHERE id=?',(market['p']['id'],)).fetchone()[0]==6


def test_commission_precedence_and_snapshot(market):
    f=market['factory']
    with f.db.transaction() as c:
        c.execute("UPDATE categories SET commission_bps=800 WHERE id='home'")
        c.execute('UPDATE stores SET commission_bps=1200 WHERE id=?',(market['a']['store_id'],))
    f.cart(market['buyer'],market['p'],3);r,_,_=f.checkout(market['buyer']);assert r.status_code==201
    s=r.json()['order']['shipments'][0];assert s['commission_fils']==560
    assert s['items'][0]['commission_bps']==1200
    with f.db.transaction() as c:c.execute('UPDATE stores SET commission_bps=3000 WHERE id=?',(market['a']['store_id'],))
    existing=market['buyer']['client'].get('/api/orders').json()['orders'][0]['shipments'][0]
    assert existing['commission_fils']==560
    # Category fallback, then global fallback.
    with f.db.transaction() as c:c.execute('UPDATE stores SET commission_bps=NULL WHERE id=?',(market['a']['store_id'],))
    f.cart(market['buyer'],market['p']);r,_,_=f.checkout(market['buyer']);assert r.json()['order']['shipments'][0]['items'][0]['commission_bps']==800
    with f.db.transaction() as c:c.execute("UPDATE categories SET commission_bps=NULL WHERE id='home'")
    f.cart(market['buyer'],market['p']);r,_,_=f.checkout(market['buyer']);assert r.json()['order']['shipments'][0]['items'][0]['commission_bps']==500


@pytest.mark.parametrize('amount,bps,expected',[(1,5000,1),(1001,500,50),(4665,1200,560),(100000,0,0),(100000,5000,50000)])
def test_integer_round_half_up(amount,bps,expected):
    assert commission(amount,bps)==expected


def test_cart_ownership_and_input_validation(market):
    f=market['factory'];assert market['a']['client'].put('/api/cart',json={'product_id':market['p']['id'],'quantity':1}).status_code==409
    client=market['buyer']['client']
    for quantity in [-1,100,1.5,True]:assert client.put('/api/cart',json={'product_id':market['p']['id'],'quantity':quantity}).status_code==422
    f.cart(market['buyer'],market['p'])
    other=f.user()['client'];assert other.get('/api/cart').json()['quantity']==0
    assert client.put('/api/cart',json={'product_id':market['p']['id'],'quantity':0}).json()['quantity']==0
