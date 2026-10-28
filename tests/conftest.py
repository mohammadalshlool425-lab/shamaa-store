"""All accounts, credentials, images and databases are generated at runtime and isolated."""
from dataclasses import replace
from io import BytesIO
import secrets
from pathlib import Path
import pyotp
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from server.config import Settings
from server.database import now
from server.application import create_app
from server.security import encrypt, hash_password


@pytest.fixture
def env(tmp_path):
    settings = Settings(env='development',db_path=tmp_path/'marketplace.sqlite3',data_dir=tmp_path,
                        allowed_hosts=['testserver'],public_url='http://testserver',demo=False,
                        email_mode='file',encryption_key='')
    app = create_app(settings)
    return app,settings


def png_bytes(color='green'):
    out=BytesIO()
    Image.new('RGB',(60,40),color).save(out,format='PNG')
    return out.getvalue()


class Factory:
    def __init__(self,app):
        self.app=app
        self.db=app.state.db
        self.settings=app.state.settings
        self.counter=0

    def client(self,**kwargs):
        c=TestClient(self.app,**kwargs)
        c.headers.update({'X-Requested-With':'shamaa','X-Language':'en'})
        return c

    def user(self,role='buyer',verified=True):
        self.counter+=1
        email=f'account-{self.counter}@example.com'
        password=secrets.token_urlsafe(24)+'a9'
        secret=pyotp.random_base32() if role=='admin' else None
        with self.db.transaction() as c:
            uid=c.execute('INSERT INTO users(name,email,password_hash,role,email_verified_at,mfa_secret,created_at) VALUES(?,?,?,?,?,?,?)',
                          ('Generated user '+str(self.counter),email,hash_password(password),role,now() if verified else None,
                           encrypt(self.settings,secret) if secret else None,now())).lastrowid
        client=self.client()
        body={'email':email,'password':password,'otp':pyotp.TOTP(secret).now() if secret else ''}
        response=client.post('/api/auth/login',json=body)
        assert response.status_code==200,response.text
        client.headers['X-CSRF-Token']=response.json()['csrf_token']
        return {'id':uid,'email':email,'password':password,'secret':secret,'client':client}

    def seller(self,shipping=2500):
        user=self.user('seller')
        with self.db.transaction() as c:
            store=c.execute("INSERT INTO stores(owner_id,slug,name_ar,name_en,city,shipping_fils,status,created_at) VALUES(?,?,?,?,'irbid',?,'active',?)",
                            (user['id'],f'store-{user["id"]}','متجر اختبار','Generated store',shipping,now())).lastrowid
        user['store_id']=store
        return user

    def product(self,seller,stock=10,price=12345,category='home',color='green'):
        upload=seller['client'].post('/api/seller/media',files={'file':('image.png',png_bytes(color),'image/png')})
        assert upload.status_code==201,upload.text
        body={'title_ar':'منتج اختبار مولّد','title_en':'Generated test product','description_ar':'وصف دقيق لغرض الاختبارات المعزولة فقط.',
              'description_en':'Accurate description for isolated tests only.','category_id':category,'price_fils':price,'stock':stock,
              'sku':secrets.token_hex(6),'media_id':upload.json()['id']}
        result=seller['client'].post('/api/seller/products',json=body)
        assert result.status_code==201,result.text
        product=result.json()['product']
        # Fixture publication only. Dedicated moderation tests exercise actual administrator endpoints.
        with self.db.transaction() as c:
            c.execute("UPDATE products SET status='active' WHERE id=?",(product['id'],))
        return product

    def cart(self,buyer,product,quantity=1):
        result=buyer['client'].put('/api/cart',json={'product_id':product['id'],'quantity':quantity})
        assert result.status_code==200,result.text
        return result.json()

    def checkout(self,buyer,quote=None,key=None,**changes):
        quote=quote or buyer['client'].get('/api/cart').json()
        body={'recipient':'Generated buyer','phone':'0791234567','city':'irbid','address':'Generated delivery address, street 1',
              'expected_total_fils':quote['total_fils'],'quote_hash':quote['quote_hash']}
        body.update(changes)
        key=key or secrets.token_urlsafe(24)
        return buyer['client'].post('/api/orders',json=body,headers={'Idempotency-Key':key}),key,body


@pytest.fixture
def factory(env):
    return Factory(env[0])


@pytest.fixture
def market(factory):
    a=factory.seller(2500)
    b=factory.seller(3500)
    buyer=factory.user()
    p=factory.product(a,stock=8,price=1555)
    q=factory.product(b,stock=5,price=10001,color='blue')
    return {'factory':factory,'a':a,'b':b,'buyer':buyer,'p':p,'q':q}
