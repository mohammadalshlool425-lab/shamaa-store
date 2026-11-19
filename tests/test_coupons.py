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
