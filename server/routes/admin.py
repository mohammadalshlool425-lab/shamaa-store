import json
from typing import Literal
from fastapi import APIRouter, BackgroundTasks, Depends, Request
from ..commerce import PRODUCT_SQL, product_dict, store_dict
from ..database import now
from ..errors import fail
from .. import mail, schemas
from ..security import admin_user, audit, recent_admin
from .orders import change_status, order_dict

router = APIRouter(prefix='/api/admin', tags=['Administration'])


@router.get('/summary')
def summary(request: Request,user=Depends(admin_user)):
    with request.app.state.db.read() as c:
        result = {name:c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for name,table in [('users','users'),('stores','stores'),('products','products'),('orders','orders')]}
        result['pending_stores'] = c.execute("SELECT COUNT(*) FROM stores WHERE status='pending'").fetchone()[0]
        result['pending_products'] = c.execute("SELECT COUNT(*) FROM products WHERE status='pending'").fetchone()[0]
        result['cod_commission_due_fils'] = c.execute('SELECT COALESCE(SUM(amount_fils),0) FROM ledger').fetchone()[0]
        result['failed_mail'] = c.execute("SELECT COUNT(*) FROM mail_outbox WHERE status='failed'").fetchone()[0]
    return result


@router.get('/users')
def users(request: Request,user=Depends(admin_user)):
    with request.app.state.db.read() as c:
        rows = c.execute('SELECT id,name,email,role,email_verified_at,active,created_at FROM users ORDER BY id DESC LIMIT 200').fetchall()
    return {'users':[dict(r) for r in rows]}


@router.patch('/users/{user_id}')
def active(user_id: int,body: schemas.UserActive,request: Request,user=Depends(admin_user)):
    if user_id==user['id']:
        fail('self_suspend',409)
    with request.app.state.db.transaction() as c:
        if not c.execute('SELECT 1 FROM users WHERE id=?',(user_id,)).fetchone():
            fail('not_found',404)
        c.execute('UPDATE users SET active=? WHERE id=?',(int(body.active),user_id))
        if not body.active:
            c.execute('DELETE FROM sessions WHERE user_id=?',(user_id,))
        audit(c,user['id'],'user_enabled' if body.active else 'user_suspended','user',user_id)
    return {'ok':True}


@router.get('/stores')
def stores(request: Request,user=Depends(admin_user)):
    with request.app.state.db.read() as c:
        rows = c.execute('SELECT s.*,u.email,u.email_verified_at FROM stores s JOIN users u ON u.id=s.owner_id ORDER BY s.id DESC LIMIT 200').fetchall()
    return {'stores':[{**store_dict(r,True),'email':r['email'],'email_verified_at':r['email_verified_at']} for r in rows]}


@router.post('/stores/{store_id}/review')
def review_store(store_id: int,body: schemas.Review,request: Request,tasks: BackgroundTasks,user=Depends(admin_user)):
    with request.app.state.db.transaction() as c:
        s = c.execute('SELECT s.*,u.email_verified_at FROM stores s JOIN users u ON u.id=s.owner_id WHERE s.id=?',(store_id,)).fetchone()
        if not s:
            fail('not_found',404)
        if s['is_demo']:
            fail('demo_disabled',409)
        if body.approved and not s['email_verified_at']:
            fail('verify_email',409)
        status = 'active' if body.approved else 'rejected'
        c.execute('UPDATE stores SET status=?,review_note=? WHERE id=?',(status,body.note,store_id))
        audit(c,user['id'],'store_'+status,'store',store_id,body.note)
        mail.notify(c,request.app.state.settings,s['owner_id'],'store_review',
                    'اكتملت مراجعة طلب متجرك. راجع الحالة والملاحظات في لوحة البائع.',
                    'Your store review is complete. Check its status and notes in the seller dashboard.')
    tasks.add_task(mail.deliver_pending,request.app.state.db,request.app.state.settings)
    return {'ok':True}


@router.get('/products')
def products(request: Request,user=Depends(admin_user)):
    with request.app.state.db.read() as c:
        rows = c.execute(PRODUCT_SQL+' ORDER BY p.id DESC LIMIT 200').fetchall()
        result = []
        for row in rows:
            item = product_dict(row,True)
            gallery = c.execute('''SELECT pi.media_id,pi.position FROM product_images pi
              JOIN media m ON m.id=pi.media_id WHERE pi.product_id=? ORDER BY pi.position,pi.id''',
                                (row['id'],)).fetchall()
            if not gallery and row['media_id']:
                gallery = [{'media_id':row['media_id'],'position':0}]
            item['images'] = [
                {'media_id':image['media_id'],'position':image['position'],
                 'url':f"/media/{image['media_id']}/480.webp"}
                for image in gallery
            ]
            variants = c.execute('''SELECT sku,attributes_json,price_fils,compare_at_fils,stock,active
              FROM product_variants WHERE product_id=? ORDER BY id''',(row['id'],)).fetchall()
            item['variants'] = [
                {'sku':variant['sku'],'attributes':json.loads(variant['attributes_json']),
                 'price_fils':variant['price_fils'],'compare_at_fils':variant['compare_at_fils'],
                 'stock':variant['stock'],'active':bool(variant['active'])}
                for variant in variants
            ]
            result.append(item)
    return {'products':result}


@router.post('/products/{product_id}/review')
def review_product(product_id: int,body: schemas.Review,request: Request,tasks: BackgroundTasks,user=Depends(admin_user)):
    with request.app.state.db.transaction() as c:
        p = c.execute(PRODUCT_SQL+' WHERE p.id=?',(product_id,)).fetchone()
        if not p:
            fail('not_found',404)
        if p['is_demo']:
            fail('demo_disabled',409)
        if body.approved and (p['store_status']!='active' or not p['media_id']):
            fail('invalid_input',409)
        status = 'active' if body.approved else 'rejected'
        c.execute('UPDATE products SET status=?,review_note=?,version=version+1,updated_at=? WHERE id=?',(status,body.note,now(),product_id))
        audit(c,user['id'],'product_'+status,'product',product_id,body.note)
        mail.notify(c,request.app.state.settings,p['owner_id'],'product_review',
                    'اكتملت مراجعة أحد منتجاتك. راجع لوحة المنتجات.', 'A product review is complete. Check your product dashboard.')
    tasks.add_task(mail.deliver_pending,request.app.state.db,request.app.state.settings)
    return {'ok':True}


@router.get('/orders')
def orders(request: Request,user=Depends(admin_user)):
    with request.app.state.db.read() as c:
        rows = c.execute('SELECT * FROM orders ORDER BY id DESC LIMIT 100').fetchall()
        return {'orders':[order_dict(c,r) for r in rows]}


@router.patch('/orders/{shipment_id}/status')
def order_status(shipment_id: int,body: schemas.StatusChange,request: Request,tasks: BackgroundTasks,user=Depends(admin_user)):
    with request.app.state.db.transaction() as c:
        sub = c.execute('SELECT * FROM seller_orders WHERE id=?',(shipment_id,)).fetchone()
        if not sub:
            fail('not_found',404)
        change_status(c,request,user,sub,body.status,body.expected_status,body.note)
    tasks.add_task(mail.deliver_pending,request.app.state.db,request.app.state.settings)
    return {'ok':True}


@router.get('/settings')
def settings(request: Request,user=Depends(admin_user)):
    with request.app.state.db.read() as c:
        result = dict(c.execute('SELECT key,value FROM settings').fetchall())
        result['commission_bps'] = int(result['commission_bps'])
        # Private settings only: public bootstrap deliberately omits commission overrides.
        result['category_commissions'] = [dict(r) for r in c.execute(
            'SELECT id,name_ar,name_en,parent_id,commission_bps FROM categories ORDER BY id')]
    return result


@router.put('/settings')
def update_settings(body: schemas.SettingsInput,request: Request,user=Depends(recent_admin)):
    with request.app.state.db.transaction() as c:
        for key,value in body.model_dump().items():
            c.execute('UPDATE settings SET value=? WHERE key=?',(str(value),key))
        audit(c,user['id'],'settings_updated','settings','global',f'commission_bps={body.commission_bps}')
    return {'ok':True}


@router.put('/commissions/{entity}/{entity_id}')
def update_commission(entity: str,entity_id: str,body: schemas.CommissionInput,request: Request,user=Depends(recent_admin)):
    table = {'store':'stores','category':'categories'}.get(entity)
    if not table:
        fail('not_found',404)
    with request.app.state.db.transaction() as c:
        result = c.execute(f'UPDATE {table} SET commission_bps=? WHERE id=?',(body.commission_bps,entity_id))
        if result.rowcount!=1:
            fail('not_found',404)
        audit(c,user['id'],'commission_updated',entity,entity_id,str(body.commission_bps))
    return {'ok':True}


@router.get('/audit')
def audit_log(request: Request,user=Depends(admin_user)):
    with request.app.state.db.read() as c:
        rows = c.execute('SELECT * FROM audit_log ORDER BY id DESC LIMIT 200').fetchall()
    return {'events':[dict(r) for r in rows]}


@router.get('/ledger')
def ledger(request: Request,user=Depends(admin_user)):
    with request.app.state.db.read() as c:
        rows = c.execute('SELECT * FROM ledger ORDER BY id DESC LIMIT 200').fetchall()
    return {'entries':[dict(r) for r in rows]}



@router.get('/reviews')
def review_queue(request: Request,
                 status: Literal['pending','approved','rejected'] = 'pending',
                 user=Depends(admin_user)):
    with request.app.state.db.read() as c:
        rows = c.execute(
            """SELECT r.id,r.order_item_id,r.product_id,r.seller_order_id,
                      r.rating,r.body,r.status,r.moderation_note,r.created_at,
                      oi.title_ar,oi.title_en,oi.variant_sku,
                      o.number AS order_number
                 FROM reviews r
                 JOIN order_items oi ON oi.id=r.order_item_id
                 JOIN seller_orders so ON so.id=r.seller_order_id
                 JOIN orders o ON o.id=so.order_id
                WHERE r.status=?
                ORDER BY r.created_at DESC,r.id DESC LIMIT 200""",
            (status,),
        ).fetchall()
    return {'reviews':[dict(row) for row in rows]}


@router.post('/reviews/{review_id}/moderate')
def moderate_review(review_id: int,body: schemas.Review,request: Request,
                    user=Depends(admin_user)):
    with request.app.state.db.transaction() as c:
        review = c.execute(
            "SELECT buyer_id,status FROM reviews WHERE id=?",
            (review_id,),
        ).fetchone()
        if not review:
            fail('not_found',404)
        if review['status'] != 'pending':
            fail('invalid_status',409)
        status = 'approved' if body.approved else 'rejected'
        moderated = now()
        changed = c.execute(
            """UPDATE reviews SET status=?,moderation_note=?,moderated_by=?,
                      moderated_at=?,updated_at=? WHERE id=? AND status='pending'""",
            (status,body.note,user['id'],moderated,moderated,review_id),
        )
        if changed.rowcount != 1:
            fail('invalid_status',409)
        audit(c,user['id'],'review_'+status,'review',review_id,body.note)
        c.execute(
            """INSERT INTO notifications(user_id,kind,body_ar,body_en,created_at)
               VALUES(?,'review',?,?,?)""",
            (review['buyer_id'],
             'تمت مراجعة تقييمك. افتح صفحة المنتج للاطلاع على حالته.',
             'Your review has been moderated. Check the product page for its status.',
             moderated),
        )
    return {'ok':True,'status':status}


@router.get('/review-reports')
def review_report_queue(request: Request,user=Depends(admin_user)):
    with request.app.state.db.read() as c:
        rows = c.execute(
            """SELECT rr.id,rr.review_id,rr.reporter_id,rr.reason,rr.details,
                      rr.status,rr.resolution_note,rr.created_at,
                      r.rating,r.body,r.status AS review_status,
                      r.product_id,oi.title_ar,oi.title_en,o.number AS order_number
                 FROM review_reports rr
                 JOIN reviews r ON r.id=rr.review_id
                 JOIN order_items oi ON oi.id=r.order_item_id
                 JOIN seller_orders so ON so.id=r.seller_order_id
                 JOIN orders o ON o.id=so.order_id
                WHERE rr.status='open'
                ORDER BY rr.created_at ASC,rr.id ASC LIMIT 200"""
        ).fetchall()
    return {'reports':[dict(row) for row in rows]}


@router.post('/review-reports/{report_id}/resolve')
def resolve_review_report(report_id: int,body: schemas.ReviewReportResolution,
                          request: Request,user=Depends(admin_user)):
    with request.app.state.db.transaction() as c:
        report = c.execute(
            "SELECT status FROM review_reports WHERE id=?",
            (report_id,),
        ).fetchone()
        if not report:
            fail('not_found',404)
        if report['status'] != 'open':
            fail('invalid_status',409)
        resolved = now()
        changed = c.execute(
            """UPDATE review_reports SET status=?,resolution_note=?,
                      resolved_by=?,resolved_at=? WHERE id=? AND status='open'""",
            (body.status,body.note,user['id'],resolved,report_id),
        )
        if changed.rowcount != 1:
            fail('invalid_status',409)
        audit(c,user['id'],'review_report_'+body.status,'review_report',report_id,body.note)
    return {'ok':True,'status':body.status}
