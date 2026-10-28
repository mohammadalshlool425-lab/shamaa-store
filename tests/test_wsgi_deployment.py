import asyncio
from dataclasses import replace
from pathlib import Path
from threading import Thread
import httpx
import pytest
from conftest import Factory,png_bytes
from server.application import create_app
from server.config import Settings
from server.database import Database
from server.wsgi import create_wsgi_app
from scripts.setup_pythonanywhere import prepare_preview


class WSGIFactory(Factory):
    def __init__(self,app):
        super().__init__(app.app)
        self.wsgi=app
        self.clients=[]

    def client(self,**kwargs):
        client=httpx.Client(transport=httpx.WSGITransport(app=self.wsgi),base_url='https://testserver',
                            headers={'X-Requested-With':'shamaa','X-Language':'en'},**kwargs)
        self.clients.append(client)
        return client


@pytest.fixture
def wsgi_factory(tmp_path):
    loop=asyncio.new_event_loop()
    thread=Thread(target=loop.run_forever,daemon=True);thread.start()
    settings=Settings(env='development',db_path=tmp_path/'marketplace.sqlite3',data_dir=tmp_path,
                      public_url='https://testserver',allowed_hosts=['testserver'],demo=False,
                      email_mode='file',encryption_key='',sqlite_journal_mode='DELETE')
    factory=WSGIFactory(create_wsgi_app(settings,loop=loop))
    yield factory
    for client in factory.clients:client.close()
    loop.call_soon_threadsafe(loop.stop);thread.join(timeout=5)
    assert not thread.is_alive()
    loop.close()


def test_wsgi_pages_static_pwa_auth_guards_and_journal(wsgi_factory):
    f=wsgi_factory;client=f.client()
    assert '<html lang="ar" dir="rtl">' in client.get('/?lang=ar').text
    assert '<html lang="en" dir="ltr">' in client.get('/?lang=en').text
    assert client.get('/healthz').json()['database']=='ready'
    assert client.get('/static/app.js').status_code==200
    assert client.get('/static/fonts/tajawal-arabic-400-normal.woff2').status_code==200
    assert client.get('/manifest.webmanifest').json()['display']=='standalone'
    assert 'shamaa-public-v1.0.3' in client.get('/sw.js').text
    assert client.get('/api/admin/summary').status_code==401
    assert client.get('/account').headers['cache-control']=='no-store'
    assert client.get('/api/auth/me').headers['cache-control']=='no-store'
    with f.db.read() as c:assert c.execute('PRAGMA journal_mode').fetchone()[0]=='delete'


def test_wsgi_cookie_csrf_and_uploaded_image_ownership(wsgi_factory):
    f=wsgi_factory;owner=f.seller();other=f.seller()
    client=f.client()
    login=client.post('/api/auth/login',json={'email':owner['email'],'password':owner['password']})
    assert login.status_code==200 and '; secure' in login.headers['set-cookie'].lower()
    assert client.post('/api/auth/logout').status_code==403
    client.headers['X-CSRF-Token']=login.json()['csrf_token']
    assert client.post('/api/auth/logout',headers={'Origin':'https://untrusted.example'}).status_code==403
    image=client.post('/api/seller/media',files={'file':('generated.png',png_bytes(),'image/png')})
    assert image.status_code==201
    url=image.json()['url']
    assert client.get(url).status_code==200 and client.get(url).headers['cache-control']=='no-store'
    assert other['client'].get(url).status_code==404
    assert f.client().get(url).status_code==404
    assert client.post('/api/seller/media',files={'file':('invalid.svg',b'<svg/>','image/svg+xml')}).status_code==415
    assert client.post('/api/auth/logout').status_code==200


def test_wsgi_order_split_idempotency_media_and_persistence(wsgi_factory):
    f=wsgi_factory;a=f.seller(2500);b=f.seller(3500);buyer=f.user()
    p=f.product(a,price=12345);q=f.product(b,price=1001,color='red')
    f.cart(buyer,p,2);quote=f.cart(buyer,q)
    response,key,body=f.checkout(buyer,quote)
    assert response.status_code==201
    order=response.json()['order']
    assert len(order['shipments'])==2 and order['total_fils']==31691
    replay=buyer['client'].post('/api/orders',json=body,headers={'Idempotency-Key':key})
    assert replay.status_code==200 and replay.json()['replayed']
    assert a['client'].get('/api/orders/'+str(order['id'])).status_code==404
    # Recreate the application with the SAME private data directory; no reset/seed.
    reopened=WSGIFactory(create_wsgi_app(f.settings,loop=f.wsgi.loop))
    try:
        client=reopened.client();client.cookies.update(buyer['client'].cookies)
        saved=client.get('/api/orders').json()['orders']
        assert len(saved)==1 and saved[0]['id']==order['id']
        assert client.get('/api/products/'+str(p['id'])).json()['product']['stock']==8
        image=client.get(p['image_url']);assert image.status_code==200 and image.content.startswith(b'RIFF')
        with reopened.db.read() as c:
            assert c.execute('SELECT COUNT(*) FROM orders').fetchone()[0]==1
            assert c.execute('PRAGMA journal_mode').fetchone()[0]=='delete'
    finally:
        for client in reopened.clients:client.close()


def test_wsgi_rejects_oversized_body_before_mutation(wsgi_factory):
    f=wsgi_factory;seller=f.seller()
    result=seller['client'].post('/api/seller/media',content=b'x'*(6*1024*1024+1),headers={'Content-Type':'application/octet-stream'})
    assert result.status_code==413
    with f.db.read() as c:assert c.execute('SELECT COUNT(*) FROM media').fetchone()[0]==0


def test_journal_mode_is_whitelisted_and_default_is_unchanged(tmp_path):
    settings=Settings(env='development',db_path=tmp_path/'db.sqlite3',data_dir=tmp_path,
                      email_mode='file',demo=False,encryption_key='')
    assert settings.sqlite_journal_mode=='WAL'
    with pytest.raises(RuntimeError):replace(settings,sqlite_journal_mode='WAL; DROP TABLE users').prepare()
    with pytest.raises(ValueError):Database(tmp_path/'bad.sqlite3','arbitrary')
    app=create_app(settings)
    with app.state.db.read() as c:assert c.execute('PRAGMA journal_mode').fetchone()[0]=='wal'


def preview_tree(tmp_path):
    home=tmp_path/'generatedowner';project=home/'shamaa-store'
    (project/'server').mkdir(parents=True)
    (project/'server'/'wsgi.py').write_text('# generated test-only project marker\n')
    return home,project


def test_preview_setup_is_private_idempotent_and_does_not_publish(tmp_path):
    home,project=preview_tree(tmp_path)
    result=prepare_preview(project,'generatedowner.pythonanywhere.com',home=home)
    assert not (result['data_dir']/'marketplace.sqlite3').exists()
    assert (project/'.env').stat().st_mode&0o077==0
    assert result['launcher'].stat().st_mode&0o077==0
    assert result['data_dir'].stat().st_mode&0o077==0
    original=(project/'.env').read_bytes()
    keep=result['data_dir']/'keep.txt';keep.write_text('Generated local marker, not actual customer data.')
    second=prepare_preview(project,'generatedowner.pythonanywhere.com',home=home)
    assert second['url']=='https://generatedowner.pythonanywhere.com'
    assert original==(project/'.env').read_bytes() and keep.exists()
    assert 'create_wsgi_app()' in result['wsgi_text']
    assert str(project/'.venv')==str(result['virtualenv'])


@pytest.mark.parametrize('domain',['someoneelse.pythonanywhere.com','generatedowner.example.com',
                                 'generatedowner.pythonanywhere.com.evil','generatedowner.pythonanywhere.com?token=value'])
def test_preview_setup_rejects_wrong_or_untrusted_domains(tmp_path,domain):
    home,project=preview_tree(tmp_path)
    with pytest.raises(ValueError):prepare_preview(project,domain,home=home)
    assert not (project/'.env').exists() and not (home/'shamaa-preview-data').exists()


def test_preview_setup_never_overwrites_other_config_or_adopts_unknown_data(tmp_path):
    home,project=preview_tree(tmp_path)
    env=project/'.env';original='SHAMAA_ENV=production\n'
    env.write_text(original)
    with pytest.raises(ValueError):prepare_preview(project,'generatedowner.pythonanywhere.com',home=home)
    assert env.read_text()==original
    env.unlink()
    data=home/'shamaa-preview-data';data.mkdir();(data/'keep.txt').write_text('Generated local marker')
    with pytest.raises(ValueError):prepare_preview(project,'generatedowner.pythonanywhere.com',home=home)
    assert not env.exists() and (data/'keep.txt').exists()


def test_wsgi_loop_is_created_on_first_request_and_not_reused_after_fork(monkeypatch):
    from server import wsgi
    calls=[]
    class Adapter:
        def __init__(self,app,wait_time,loop):
            calls.append((app,wait_time,loop));self.loop=object()
        def __call__(self,environ,start_response):
            return [b'generated adapter response']
    marker=object()
    monkeypatch.setattr(wsgi,'ASGIMiddleware',Adapter)
    monkeypatch.setattr(wsgi,'create_app',lambda _:marker)
    monkeypatch.setattr(wsgi,'getpid',lambda:100)
    app=wsgi.create_wsgi_app()
    assert not calls
    assert list(app({},None))==[b'generated adapter response']
    assert len(calls)==1 and calls[0][0] is marker and calls[0][1]==20
    parent_loop=app.loop
    app({},None);assert len(calls)==1
    monkeypatch.setattr(wsgi,'getpid',lambda:200)
    app({},None)
    assert len(calls)==2 and calls[1][2] is None and app.loop is not parent_loop
