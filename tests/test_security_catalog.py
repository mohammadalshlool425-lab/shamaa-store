from dataclasses import replace
from io import BytesIO
import secrets
import sqlite3
import pyotp
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from server.config import Settings
from server.database import now
from server.application import create_app
from server.security import COOKIE, digest
from conftest import png_bytes


def test_no_seeded_users_or_fake_payments(factory):
    with factory.db.read() as c:
        assert c.execute('SELECT COUNT(*) FROM users').fetchone()[0]==0
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='payments'").fetchone()
    client=factory.client()
    assert client.post('/api/buy/verification',json={}).status_code==404
    assert client.post('/api/feature',json={}).status_code==404


def test_anonymous_custom_header_cross_origin_and_authenticated_csrf(factory):
    user=factory.user();client=user['client']
    assert TestClient(factory.app).post('/api/auth/forgot-password',json={'email':user['email']}).status_code==403
    assert client.post('/api/auth/logout',headers={'X-CSRF-Token':'wrong'}).status_code==403
    assert client.post('/api/auth/logout',headers={'Origin':'https://evil.example'}).status_code==403
    assert client.get('/api/auth/me').json()['user']
    assert client.post('/api/auth/logout',headers={'Origin':'http://testserver'}).status_code==200


def test_cookie_security_headers_and_private_no_store(factory):
    user=factory.user();r=user['client'].get('/api/auth/me')
    assert r.headers['cache-control']=='no-store'
    assert "script-src 'self'" in r.headers['content-security-policy']
    assert "object-src 'none'" in r.headers['content-security-policy']
    assert r.headers['x-content-type-options']=='nosniff'
    assert r.headers['referrer-policy']=='no-referrer'
    fresh=factory.client();r=fresh.post('/api/auth/login',json={'email':user['email'],'password':user['password']})
    cookie=r.headers['set-cookie'].lower()
    assert 'httponly' in cookie and 'samesite=lax' in cookie
    assert fresh.get('/api/auth/sessions').headers['cache-control']=='no-store'


def test_host_allowlist_rejects_unknown_hosts(factory):
    assert factory.client().get('/healthz',headers={'Host':'evil.example'}).status_code==400


@pytest.mark.parametrize('path',['/api/admin/summary','/api/admin/users','/api/admin/settings','/api/admin/orders','/api/admin/ledger','/api/admin/audit'])
def test_administration_requires_server_side_role_and_mfa(factory,path):
    buyer=factory.user();assert buyer['client'].get(path).status_code==403
    assert factory.client().get(path).status_code==401


def test_administrator_review_and_financial_step_up(factory):
    admin=factory.user('admin');seller=factory.user();client=seller['client'];a=admin['client']
    body={'slug':'review-store','name_ar':'متجر جديد','name_en':'New store','city':'irbid'}
    r=client.post('/api/seller/store',json=body);assert r.status_code==201
    sid=r.json()['store']['id']
    assert client.get('/api/seller/store').json()['store']['status']=='pending'
    assert a.post(f'/api/admin/stores/{sid}/review',json={'approved':True,'note':'Basic email and submitted details reviewed'}).status_code==200
    image=client.post('/api/seller/media',files={'file':('test.png',png_bytes(),'image/png')}).json()['id']
    p={'title_ar':'عنوان منتج واضح','title_en':'Clear product title','description_ar':'وصف دقيق للمنتج الجديد.','description_en':'An accurate new product description.',
       'category_id':'home','price_fils':12000,'stock':3,'sku':'REVIEW','media_id':image}
    r=client.post('/api/seller/products',json=p);assert r.status_code==201,r.text;pid=r.json()['product']['id']
    assert factory.client().get(f'/api/products/{pid}').status_code==404
    assert factory.client().get(f'/media/{image}/480.webp').status_code==404
    assert a.get(f'/media/{image}/480.webp').status_code==200
    assert a.post(f'/api/admin/products/{pid}/review',json={'approved':True}).status_code==200
    assert factory.client().get(f'/api/products/{pid}').status_code==200
    assert factory.client().get(f'/media/{image}/480.webp').status_code==200
    settings={'commission_bps':700,'home_title_ar':'عنوان رئيسية جديد','home_title_en':'A new homepage headline'}
    with factory.db.transaction() as c:c.execute('UPDATE sessions SET mfa_at=? WHERE user_id=?',(now()-700,admin['id']))
    r=a.put('/api/admin/settings',json=settings);assert r.status_code==403 and r.json()['code']=='reauth'
    otp=pyotp.TOTP(admin['secret']).at(now()+30)
    assert a.post('/api/auth/reauth',json={'password':admin['password'],'otp':otp}).status_code==200
    assert a.put('/api/admin/settings',json=settings).status_code==200
    assert a.get('/api/admin/settings').json()['commission_bps']==700
    assert a.get('/api/admin/audit').json()['events']


def test_suspension_revokes_sessions_and_hides_store_products(factory):
    admin=factory.user('admin');seller=factory.seller();p=factory.product(seller)
    assert factory.client().get('/api/products/'+str(p['id'])).status_code==200
    assert admin['client'].patch('/api/admin/users/'+str(seller['id']),json={'active':False}).status_code==200
    assert seller['client'].get('/api/seller/products').status_code==401
    assert factory.client().get('/api/products/'+str(p['id'])).status_code==404
    assert admin['client'].patch('/api/admin/users/'+str(admin['id']),json={'active':False}).status_code==409


def test_owner_only_product_stock_and_media(factory):
    owner=factory.seller();other=factory.seller();p=factory.product(owner)
    assert other['client'].patch('/api/seller/products/'+str(p['id'])+'/stock',json={'stock':99,'expected_version':p['version']}).status_code==404
    private=owner['client'].post('/api/seller/media',files={'file':('photo.png',png_bytes('red'),'image/png')}).json()
    assert factory.client().get(private['url']).status_code==404
    assert other['client'].get(private['url']).status_code==404
    assert owner['client'].get(private['url']).headers['cache-control']=='no-store'


@pytest.mark.parametrize('bad,mime',[(b'<svg onload="alert(1)"/>','image/svg+xml'),(b'not an image','image/png'),(b'\x89PNG\r\n\x1a\ninvalid','image/png')])
def test_upload_rejects_invalid_content_even_if_mime_claims_image(factory,bad,mime):
    seller=factory.seller()
    r=seller['client'].post('/api/seller/media',files={'file':('image.png',bad,mime)})
    assert r.status_code==415


def test_upload_limits_and_normalization_deduplication(factory):
    seller=factory.seller();client=seller['client']
    assert client.post('/api/seller/media',files={'file':('large.png',b'x'*(5*1024*1024+1),'image/png')}).status_code==413
    a=client.post('/api/seller/media',files={'file':('../../unsafe.png',png_bytes(),'text/plain')})
    assert a.status_code==201 # true decoded type, not the caller's MIME or filename
    b=client.post('/api/seller/media',files={'file':('same.png',png_bytes(),'image/png')})
    assert b.json()['duplicate'] and b.json()['id']==a.json()['id']
    data=client.get(a.json()['url']).content
    image=Image.open(BytesIO(data));assert image.format=='WEBP' and image.size==(60,40)
    assert not image.getexif()
    with factory.db.read() as c:assert c.execute('SELECT COUNT(*) FROM media').fetchone()[0]==1


def test_image_quota_is_checked_on_server(factory):
    seller=factory.seller()
    with factory.db.transaction() as c:
        for i in range(50):c.execute('INSERT INTO media(owner_id,digest,storage_key,bytes,created_at) VALUES(?,?,?,?,?)',(seller['id'],str(i),f'key-{i}',1,now()))
    r=seller['client'].post('/api/seller/media',files={'file':('test.png',png_bytes(),'image/png')})
    assert r.status_code==413 and r.json()['code']=='media_quota'


def test_search_filters_sort_and_parameter_binding(factory):
    seller=factory.seller();p=factory.product(seller,price=12001);q=factory.product(seller,price=1000,category='audio',color='red')
    public=factory.client()
    assert [x['id'] for x in public.get('/api/products?sort=price_asc').json()['products']]==[q['id'],p['id']]
    assert [x['id'] for x in public.get('/api/products?category=electronics').json()['products']]==[q['id']]
    assert public.get('/api/products?min_price=12000&max_price=13000').json()['total']==1
    assert public.get('/api/products?city=amman').json()['total']==0
    assert public.get('/api/products?q=%27%20OR%201=1--').json()['total']==0
    assert public.get('/api/products?limit=-1').status_code==422
    assert public.get('/api/products?sort=arbitrary_sql').status_code==422
    result=public.get('/api/products?limit=1&page=2').json();assert len(result['products'])==1 and result['pages']==2
    for row in result['products']:
        for key in ['owner_id','password_hash','commission_bps','review_note']:
            assert key not in row


def test_product_invalid_money_and_cross_owner_media_rejected(factory):
    seller=factory.seller();other=factory.seller();p=factory.product(seller)
    body={k:p[k] for k in ['category_id','title_ar','title_en','description_ar','description_en','price_fils','stock','sku','media_id']}
    body['sku']='ANOTHER';body['price_fils']=1.25
    assert seller['client'].post('/api/seller/products',json=body).status_code==422
    body['price_fils']=1000;body['compare_at_fils']=500
    assert seller['client'].post('/api/seller/products',json=body).status_code==422
    body.pop('compare_at_fils')
    assert other['client'].post('/api/seller/products',json=body).status_code==403


@pytest.mark.parametrize('table',['audit_log','ledger','order_events','inventory_movements'])
def test_sensitive_logs_append_only(factory,table):
    # BEFORE triggers execute even for a row-less modification? SQLite only executes per row,
    # so verify trigger existence and exercise audit below with an actual row.
    with factory.db.read() as c:
        definitions=c.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND tbl_name=?",(table,)).fetchall()
        assert len(definitions)==2
        assert all('RAISE(ABORT' in r[0] for r in definitions)
    if table=='audit_log':
        user=factory.user()
        with pytest.raises(sqlite3.IntegrityError):
            with factory.db.transaction() as c:c.execute("UPDATE audit_log SET action='tampered'")
        with pytest.raises(sqlite3.IntegrityError):
            with factory.db.transaction() as c:c.execute('DELETE FROM audit_log')


def test_localized_errors_do_not_echo_secret_inputs(factory):
    client=factory.client();secret=secrets.token_urlsafe(12)
    en=client.post('/api/auth/login',json={'email':'wrong','password':secret})
    assert en.status_code==422 and secret not in en.text and 'Please' in en.json()['message']
    ar=client.post('/api/auth/login',json={'email':'wrong','password':secret},headers={'X-Language':'ar'})
    assert 'تحقّق' in ar.json()['message']


def test_seo_pwa_and_not_found_status(factory):
    client=factory.client()
    assert client.get('/does-not-exist').status_code==404
    assert client.get('/product/999').status_code==404
    assert client.get('/api/does-not-exist').status_code==404
    assert client.get('/api/does-not-exist').headers['content-type'].startswith('application/json')
    for path in ['/','/search','/help','/sw.js','/manifest.webmanifest','/sitemap.xml','/robots.txt']:
        assert client.get(path).status_code==200
    assert client.get('/sw.js').headers['service-worker-allowed']=='/'
    assert 'Disallow: /' in client.get('/robots.txt').text
    assert 'noindex' in client.get('/account').text
    assert '/api/' not in client.get('/sitemap.xml').text


@pytest.mark.parametrize('changes',[
 {'demo':True},{'email_mode':'file'},{'encryption_key':''},{'public_url':'http://example.com'},
 {'allowed_hosts':['*']},{'smtp_host':''}
])
def test_production_fail_closed(factory,changes):
    base=replace(factory.settings,env='production',demo=False,email_mode='smtp',smtp_host='mail.example.com',
                 public_url='https://example.com',allowed_hosts=['example.com'])
    with pytest.raises(RuntimeError):create_app(replace(base,**changes))


def test_secure_cookie_in_production_and_no_demo_catalog(factory):
    user=factory.user()
    s=replace(factory.settings,env='production',demo=False,email_mode='smtp',smtp_host='mail.example.com',
              public_url='https://testserver',allowed_hosts=['testserver'])
    app=create_app(s);client=TestClient(app,base_url='https://testserver')
    r=client.post('/api/auth/login',headers={'X-Requested-With':'shamaa'},json={'email':user['email'],'password':user['password']})
    assert r.status_code==200 and '; secure' in r.headers['set-cookie'].lower()
    assert r.headers['strict-transport-security'].startswith('max-age=')


def test_memory_hard_password_work_is_bounded_without_weakening_hash_parameters(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Lock
    from time import sleep
    from server import security
    guard=Lock();active=0;peak=0
    def scrypt(*args,**kwargs):
        nonlocal active,peak
        assert kwargs['n']==32768 and kwargs['r']==8 and kwargs['p']==1
        with guard:active+=1;peak=max(peak,active)
        sleep(.02)
        with guard:active-=1
        return b'generated-only-for-the-concurrency-test'
    monkeypatch.setattr(security.hashlib,'scrypt',scrypt)
    with ThreadPoolExecutor(max_workers=8) as pool:
        hashes=list(pool.map(security.hash_password,['generated-only']*8))
        assert all(pool.map(lambda h:security.verify_password('generated-only',h),hashes))
    assert peak==2


def test_invalid_unicode_is_a_validation_error_and_never_echoes_password(factory):
    response=factory.client().post('/api/auth/register',content=b'{"name":"Generated","email":"generated@example.com","password":"generated9A\\ud800"}',headers={'Content-Type':'application/json'})
    assert response.status_code==422 and response.json()['code']=='invalid_input'
    assert 'generated9A' not in response.text
