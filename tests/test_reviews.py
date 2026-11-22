import sqlite3


def _deliver_review_item(factory,seller,buyer,product):
    factory.cart(buyer,product,1)
    response,_,_=factory.checkout(buyer)
    assert response.status_code==201,response.text
    order=response.json()['order']
    shipment=next(item for item in order['shipments'] if item['store_id']==seller['store_id'])
    previous='new'
    for status in ('confirmed','preparing','shipped','delivered'):
        result=seller['client'].patch(
            f"/api/seller/orders/{shipment['id']}/status",
            json={'status':status,'expected_status':previous},
        )
        assert result.status_code==200,result.text
        previous=status
    return order,shipment


def test_reviews_reject_undelivered_orders_and_require_buyers_own_delivered_items(market):
    factory=market['factory']
    buyer=market['buyer']['client']
    other=factory.user()['client']
    factory.cart(market['buyer'],market['p'],1)
    response,_,_=factory.checkout(market['buyer'])
    assert response.status_code==201,response.text
    with factory.db.read() as c:
        item_id=c.execute(
            "SELECT id FROM order_items WHERE product_id=?",
            (market['p']['id'],),
        ).fetchone()['id']
    pending={'order_item_id':item_id,'rating':5,'body':'طلب لم يصل بعد.'}
    assert buyer.post('/api/reviews',json=pending).status_code==404
    assert buyer.get('/api/reviews/eligible').json()['items']==[]

    # Complete a separate delivered order: another buyer still cannot use its line id.
    _,shipment=_deliver_review_item(factory,market['a'],market['buyer'],market['p'])
    item=next(x for x in buyer.get('/api/reviews/eligible').json()['items']
              if x['product_id']==market['p']['id'])
    payload={'order_item_id':item['order_item_id'],'rating':5,'body':'تجربة شراء جيدة جدًا.'}
    assert other.post('/api/reviews',json=payload).status_code==404
    first=buyer.post('/api/reviews',json=payload)
    assert first.status_code==201,first.text
    assert first.json()['status']=='pending'
    duplicate=buyer.post('/api/reviews',json=payload)
    assert duplicate.status_code==409 and duplicate.json()['code']=='review_exists'
    assert item['order_item_id'] not in {
        row['order_item_id'] for row in buyer.get('/api/reviews/eligible').json()['items']
    }
    with factory.db.read() as c:
        saved=c.execute("SELECT buyer_id,product_id,seller_order_id,status FROM reviews WHERE id=?",
                        (first.json()['review_id'],)).fetchone()
        assert saved['buyer_id']==market['buyer']['id']
        assert saved['product_id']==market['p']['id']
        assert saved['seller_order_id'] == item['seller_order_id'] == shipment['id']
        assert saved['status']=='pending'


def test_reviews_are_hidden_until_admin_approval_and_never_publish_identity(market):
    factory=market['factory']
    buyer=market['buyer']['client']
    _deliver_review_item(factory,market['a'],market['buyer'],market['p'])
    item=next(x for x in buyer.get('/api/reviews/eligible').json()['items']
              if x['product_id']==market['p']['id'])
    created=buyer.post('/api/reviews',json={
        'order_item_id':item['order_item_id'],'rating':4,'body':'جودة جيدة ووصل المنتج كما وُصف.'
    })
    assert created.status_code==201,created.text
    product_id=market['p']['id']
    hidden=buyer.get(f'/api/products/{product_id}/reviews').json()
    assert hidden['review_count']==0 and hidden['reviews']==[]

    admin=factory.user('admin')['client']
    queue=admin.get('/api/admin/reviews?status=pending')
    assert queue.status_code==200,queue.text
    review=next(x for x in queue.json()['reviews'] if x['id']==created.json()['review_id'])
    assert review['order_number']
    assert 'buyer_id' not in review and 'email' not in review
    moderated=admin.post(f"/api/admin/reviews/{review['id']}/moderate",
                         json={'approved':True,'note':'مراجعة مكتملة'})
    assert moderated.status_code==200,moderated.text

    english=buyer.get(f'/api/products/{product_id}/reviews').json()
    assert english['reviews'][0]['reviewer_label']=='Verified buyer'
    visible_response=buyer.get(
        f'/api/products/{product_id}/reviews',headers={'X-Language':'ar'}
    )
    assert visible_response.status_code==200,visible_response.text
    visible=visible_response.json()
    assert visible['review_count']==1 and visible['average_rating']==4
    public=visible['reviews'][0]
    assert public['verified_purchase'] is True
    assert public['reviewer_label']=='مشتري موثّق'
    assert not {'buyer_id','email','name','order_number'} & set(public)


def test_review_reports_are_unique_private_and_never_auto_hide_public_reviews(market):
    factory=market['factory']
    buyer=market['buyer']['client']
    _deliver_review_item(factory,market['a'],market['buyer'],market['p'])
    item=next(x for x in buyer.get('/api/reviews/eligible').json()['items']
              if x['product_id']==market['p']['id'])
    created=buyer.post('/api/reviews',json={
        'order_item_id':item['order_item_id'],'rating':3,'body':'ملاحظة عامة على المنتج.'
    })
    assert created.status_code==201,created.text
    admin=factory.user('admin')['client']
    admin.post(f"/api/admin/reviews/{created.json()['review_id']}/moderate",
               json={'approved':True,'note':''})
    review_id=created.json()['review_id']
    assert buyer.post(f'/api/reviews/{review_id}/reports',
                      json={'reason':'abuse','details':'بلاغ من صاحب التقييم'}).status_code==404
    reporter=factory.user()['client']
    report=reporter.post(f'/api/reviews/{review_id}/reports',
                         json={'reason':'incorrect','details':'أعتقد أن المحتوى غير دقيق.'})
    assert report.status_code==201,report.text
    duplicate=reporter.post(f'/api/reviews/{review_id}/reports',
                            json={'reason':'abuse','details':'بلاغ مكرر'})
    assert duplicate.status_code==409
    assert duplicate.json()['code']=='review_report_exists'
    assert reporter.get(f"/api/products/{market['p']['id']}/reviews").json()['review_count']==1
    queue=admin.get('/api/admin/review-reports')
    assert queue.status_code==200
    row=next(x for x in queue.json()['reports'] if x['id']==report.json()['report_id'])
    resolved=admin.post(f"/api/admin/review-reports/{row['id']}/resolve",
                        json={'status':'dismissed','note':'تمت المراجعة'})
    assert resolved.status_code==200,resolved.text
    assert admin.get('/api/admin/review-reports').json()['reports']==[]
    assert reporter.get(f"/api/products/{market['p']['id']}/reviews").json()['review_count']==1


def test_database_rejects_forged_review_purchase_self_report_and_non_admin_moderation(market):
    factory=market['factory']
    buyer=market['buyer']['client']
    _deliver_review_item(factory,market['a'],market['buyer'],market['p'])
    eligible=next(x for x in buyer.get('/api/reviews/eligible').json()['items']
                  if x['product_id']==market['p']['id'])
    created=buyer.post('/api/reviews',json={
        'order_item_id':eligible['order_item_id'],'rating':5,'body':'تقييم صحيح.'
    })
    assert created.status_code==201,created.text
    review_id=created.json()['review_id']
    stranger=factory.user()
    with factory.db.transaction() as c:
        statements=[
            ("""INSERT INTO reviews(order_item_id,buyer_id,product_id,seller_order_id,rating,body,status,created_at,updated_at)
                VALUES(?,?,?,?,?,'forged','pending',1,1)""",
             (eligible['order_item_id'],stranger['id'],market['p']['id'],eligible['seller_order_id'],5)),
            ("""INSERT INTO review_reports(review_id,reporter_id,reason,details,created_at)
                VALUES(?,?,?,?,1)""",(review_id,market['buyer']['id'],'abuse','self report')),
        ]
        for statement,params in statements:
            try:
                c.execute(statement,params)
            except sqlite3.IntegrityError:
                continue
            raise AssertionError('Database must reject forged review ownership or self-report')

        try:
            c.execute("""UPDATE reviews SET status='approved',moderation_note='forged',
                         moderated_by=?,moderated_at=1,updated_at=1 WHERE id=?""",
                      (stranger['id'],review_id))
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError('Database must reject non-admin review moderation')

