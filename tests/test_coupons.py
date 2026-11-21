import sqlite3

import pytest


def coupon_payload(**overrides):
    body = {
        'code': 'SAVE10',
        'discount_type': 'percent',
        'discount_value': 1000,
        'minimum_subtotal_fils': 500,
        'maximum_discount_fils': 2000,
        'starts_at': None,
        'expires_at': None,
        'max_uses': 20,
        'max_uses_per_buyer': 2,
        'active': True,
    }
    body.update(overrides)
    return body


def test_seller_can_create_and_list_own_coupon(factory):
    seller = factory.seller()
    response = seller['client'].post('/api/seller/coupons', json=coupon_payload(code='save10'))
    assert response.status_code == 201, response.text
    coupon = response.json()['coupon']
    assert coupon['code'] == 'SAVE10'
    assert coupon['store_id'] == seller['store_id']
    assert coupon['used_count'] == 0
    listed = seller['client'].get('/api/seller/coupons')
    assert listed.status_code == 200
    assert [item['id'] for item in listed.json()['coupons']] == [coupon['id']]


def test_seller_cannot_read_or_update_another_store_coupon(factory):
    first = factory.seller()
    second = factory.seller()
    created = first['client'].post('/api/seller/coupons', json=coupon_payload())
    assert created.status_code == 201, created.text
    coupon_id = created.json()['coupon']['id']
    assert second['client'].get('/api/seller/coupons').json()['coupons'] == []
    response = second['client'].put(f'/api/seller/coupons/{coupon_id}', json=coupon_payload(code='OTHER10'))
    assert response.status_code == 404


def test_coupon_code_is_unique_and_validation_is_strict(factory):
    seller = factory.seller()
    first = seller['client'].post('/api/seller/coupons', json=coupon_payload())
    assert first.status_code == 201, first.text
    duplicate = seller['client'].post('/api/seller/coupons', json=coupon_payload(code='save10'))
    assert duplicate.status_code == 409
    invalid_percent = seller['client'].post('/api/seller/coupons', json=coupon_payload(code='TOOHIGH', discount_value=10001))
    assert invalid_percent.status_code == 422
    invalid_dates = seller['client'].post('/api/seller/coupons', json=coupon_payload(
        code='BADDATE', starts_at=200, expires_at=100))
    assert invalid_dates.status_code == 422


def test_coupon_cannot_lower_max_uses_below_redemptions(factory):
    seller = factory.seller()
    created = seller['client'].post('/api/seller/coupons', json=coupon_payload(max_uses=10))
    assert created.status_code == 201, created.text
    coupon_id = created.json()['coupon']['id']
    with factory.db.transaction() as c:
        c.execute('UPDATE coupons SET used_count=3 WHERE id=?', (coupon_id,))
    response = seller['client'].put(
        f'/api/seller/coupons/{coupon_id}',
        json=coupon_payload(max_uses=2),
    )
    assert response.status_code == 422


def test_buyer_cannot_manage_seller_coupons(factory):
    buyer = factory.user()
    assert buyer['client'].get('/api/seller/coupons').status_code == 403
    assert buyer['client'].post('/api/seller/coupons', json=coupon_payload()).status_code == 403


def test_coupon_preview_and_checkout_discount_only_its_store(factory):
    seller = factory.seller(shipping=2500)
    other = factory.seller(shipping=3500)
    buyer = factory.user()
    product = factory.product(seller, price=10000)
    other_product = factory.product(other, price=20000, color='blue')
    created = seller['client'].post('/api/seller/coupons', json=coupon_payload(
        discount_type='percent', discount_value=1000, maximum_discount_fils=1500))
    assert created.status_code == 201, created.text
    factory.cart(buyer, product, 1)
    factory.cart(buyer, other_product, 1)
    preview = buyer['client'].post('/api/coupon-quote', json={'code':'SAVE10'})
    assert preview.status_code == 200, preview.text
    quote = buyer['client'].get('/api/cart').json()
    assert preview.json()['discount_fils'] == 1000
    assert preview.json()['total_fils'] == quote['total_fils'] - 1000
    response, _, _ = factory.checkout(buyer, quote=quote, expected_total_fils=quote['total_fils']-1000,
                                      coupon_code='save10')
    assert response.status_code == 201, response.text
    order = response.json()['order']
    assert order['discount_fils'] == 1000
    by_store = {shipment['store_id']:shipment for shipment in order['shipments']}
    assert by_store[seller['store_id']]['discount_fils'] == 1000
    assert by_store[other['store_id']]['discount_fils'] == 0
    with factory.db.read() as c:
        redemption = c.execute('SELECT * FROM coupon_redemptions WHERE order_id=?',(order['id'],)).fetchone()
        saved_coupon = c.execute('SELECT used_count FROM coupons WHERE id=?',(created.json()['coupon']['id'],)).fetchone()
    assert redemption['discount_fils'] == 1000
    assert saved_coupon['used_count'] == 1
    assert order['total_fils'] == order['subtotal_fils'] + order['shipping_fils'] - order['discount_fils']
    assert by_store[seller['store_id']]['net_fils'] == (
        by_store[seller['store_id']]['subtotal_fils']
        + by_store[seller['store_id']]['shipping_fils']
        - by_store[seller['store_id']]['discount_fils']
        - by_store[seller['store_id']]['commission_fils']
    )
    with pytest.raises(sqlite3.IntegrityError):
        with factory.db.transaction() as c:
            c.execute('UPDATE orders SET total_fils=total_fils+1 WHERE id=?', (order['id'],))
    with pytest.raises(sqlite3.IntegrityError):
        with factory.db.transaction() as c:
            c.execute('UPDATE seller_orders SET net_fils=net_fils+1 WHERE order_id=? AND store_id=?',
                      (order['id'], seller['store_id']))


def test_coupon_checkout_rejects_coupon_from_store_not_in_cart(factory):
    seller = factory.seller()
    other = factory.seller()
    buyer = factory.user()
    product = factory.product(seller)
    created = other['client'].post('/api/seller/coupons', json=coupon_payload())
    assert created.status_code == 201, created.text
    quote = factory.cart(buyer, product)
    preview = buyer['client'].post('/api/coupon-quote', json={'code':'SAVE10'})
    assert preview.status_code == 409
    response, _, _ = factory.checkout(buyer, quote=quote, expected_total_fils=quote['total_fils'], coupon_code='SAVE10')
    assert response.status_code == 409


def test_coupon_usage_limit_is_enforced_at_checkout(factory):
    seller = factory.seller()
    buyer = factory.user()
    product = factory.product(seller, price=10000)
    created = seller['client'].post('/api/seller/coupons', json=coupon_payload(max_uses=1))
    assert created.status_code == 201, created.text
    factory.cart(buyer, product)
    quote = buyer['client'].get('/api/cart').json()
    preview = buyer['client'].post('/api/coupon-quote', json={'code':'SAVE10'})
    assert preview.status_code == 200
    response, _, _ = factory.checkout(buyer, quote=quote, expected_total_fils=preview.json()['total_fils'], coupon_code='SAVE10')
    assert response.status_code == 201, response.text
    another = factory.user()
    factory.cart(another, product)
    second_preview = another['client'].post('/api/coupon-quote', json={'code':'SAVE10'})
    assert second_preview.status_code == 409
