"""Verified-purchase reviews; public output omits buyer identity."""
import json

from fastapi import APIRouter, Depends, Request

from .. import schemas
from ..database import now
from ..errors import fail, language
from ..security import audit, require_user, throttle, verified_user

router = APIRouter(prefix='/api', tags=['Verified reviews'])


@router.get('/reviews/eligible')
def eligible_reviews(request: Request,user=Depends(require_user)):
    with request.app.state.db.read() as c:
        rows = c.execute(
            """SELECT oi.id AS order_item_id,oi.product_id,oi.seller_order_id,
                      oi.title_ar,oi.title_en,oi.variant_sku,oi.variant_attributes,
                      oi.unit_price_fils,oi.image_url,o.number AS order_number,
                      so.updated_at AS delivered_at
                 FROM order_items oi
                 JOIN seller_orders so ON so.id=oi.seller_order_id
                 JOIN orders o ON o.id=so.order_id
                WHERE o.buyer_id=? AND so.status='delivered'
                  AND NOT EXISTS (SELECT 1 FROM reviews r WHERE r.order_item_id=oi.id)
                ORDER BY so.updated_at DESC,oi.id DESC LIMIT 100""",
            (user['id'],),
        ).fetchall()
    items=[]
    for row in rows:
        item=dict(row)
        attributes=json.loads(item.pop('variant_attributes') or '{}')
        item['variant_label']=' · '.join(str(value) for value in attributes.values())
        items.append(item)
    return {'items':items}


@router.post('/reviews',status_code=201)
def create_review(body: schemas.ReviewCreate,request: Request,
                  user=Depends(verified_user)):
    throttle(request,'review_create',limit=15,window=3600,identifier=str(user['id']))
    with request.app.state.db.transaction() as c:
        eligible=c.execute(
            """SELECT oi.product_id,oi.seller_order_id
                 FROM order_items oi
                 JOIN seller_orders so ON so.id=oi.seller_order_id
                 JOIN orders o ON o.id=so.order_id
                WHERE oi.id=? AND o.buyer_id=? AND so.status='delivered'""",
            (body.order_item_id,user['id']),
        ).fetchone()
        if not eligible:
            fail('not_found',404)
        created=now()
        inserted=c.execute(
            """INSERT INTO reviews(
                 order_item_id,buyer_id,product_id,seller_order_id,
                 rating,body,status,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,'pending',?,?)
               ON CONFLICT(order_item_id) DO NOTHING RETURNING id""",
            (body.order_item_id,user['id'],eligible['product_id'],
             eligible['seller_order_id'],body.rating,body.body,created,created),
        ).fetchone()
        if not inserted:
            fail('review_exists',409)
        review_id=inserted['id']
        audit(c,user['id'],'review_submitted','review',review_id,
              f"order_item={body.order_item_id};rating={body.rating}")
    return {'ok':True,'review_id':review_id,'status':'pending'}


@router.get('/products/{product_id}/reviews')
def public_product_reviews(product_id: int,request: Request):
    with request.app.state.db.read() as c:
        product=c.execute(
            """SELECT p.id FROM products p
                 JOIN stores s ON s.id=p.store_id
                 JOIN users u ON u.id=s.owner_id
                WHERE p.id=? AND p.status='active'
                  AND s.status='active' AND u.active=TRUE""",
            (product_id,),
        ).fetchone()
        if not product:
            fail('not_found',404)
        aggregate=c.execute(
            """SELECT COUNT(*) AS review_count,COALESCE(SUM(rating),0) AS rating_sum
                 FROM reviews WHERE product_id=? AND status='approved'""",
            (product_id,),
        ).fetchone()
        rows=c.execute(
            """SELECT id,rating,body,created_at FROM reviews
                WHERE product_id=? AND status='approved'
                ORDER BY created_at DESC,id DESC LIMIT 30""",
            (product_id,),
        ).fetchall()
    count=int(aggregate['review_count'])
    average=round(int(aggregate['rating_sum'])/count,2) if count else None
    label='مشتري موثّق' if language(request)=='ar' else 'Verified buyer'
    return {'review_count':count,'average_rating':average,'reviews':[
        {'id':row['id'],'rating':row['rating'],'body':row['body'],
         'created_at':row['created_at'],'reviewer_label':label,'verified_purchase':True}
        for row in rows
    ]}


@router.post('/reviews/{review_id}/reports',status_code=201)
def report_review(review_id: int,body: schemas.ReviewReportCreate,
                  request: Request,user=Depends(require_user)):
    throttle(request,'review_report',limit=10,window=3600,identifier=str(user['id']))
    with request.app.state.db.transaction() as c:
        review=c.execute(
            """SELECT id FROM reviews
                WHERE id=? AND status='approved' AND buyer_id<>?""",
            (review_id,user['id']),
        ).fetchone()
        if not review:
            fail('not_found',404)
        inserted=c.execute(
            """INSERT INTO review_reports(review_id,reporter_id,reason,details,created_at)
               VALUES(?,?,?,?,?) ON CONFLICT(review_id,reporter_id) DO NOTHING
               RETURNING id""",
            (review_id,user['id'],body.reason,body.details,now()),
        ).fetchone()
        if not inserted:
            fail('review_report_exists',409)
        audit(c,user['id'],'review_report_submitted','review',review_id,
              f"report={inserted['id']};reason={body.reason}")
    return {'ok':True,'report_id':inserted['id'],'status':'open'}
