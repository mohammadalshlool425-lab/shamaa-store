"""Disposable browser-test server. It NEVER uses data/marketplace.sqlite3."""
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from PIL import Image
import pyotp
from server.config import ROOT, Settings
from server.application import create_app
from server.database import insert_id, now
from server.security import encrypt, hash_password

# Reuse the same isolated factory as API tests to prepare one real shipment case.
sys.path.insert(0,str(ROOT/'tests'))
from conftest import Factory

folder=ROOT/'data'/'browser-tests'
if folder.exists():
    shutil.rmtree(folder) # this exact, dedicated directory only
folder.mkdir(parents=True,mode=0o700)
settings=Settings(env='development',db_path=folder/'marketplace.sqlite3',data_dir=folder,
                  demo=True,email_mode='file',encryption_key='',
                  public_url='http://127.0.0.1:8011',allowed_hosts=['127.0.0.1','localhost','testserver','*.e2b.app'])
app=create_app(settings)
credentials={}
for role in ('buyer','seller','admin'):
    password=secrets.token_urlsafe(24)+'a9'
    secret=pyotp.random_base32() if role=='admin' else None
    email=role+'-'+secrets.token_hex(4)+'@example.com'
    # The seller starts as a verified buyer, then applies for a store through the UI.
    db_role='buyer' if role=='seller' else role
    with app.state.db.transaction() as c:
        uid=insert_id(c, 'INSERT INTO users(name,email,password_hash,role,email_verified_at,mfa_secret,created_at) VALUES(?,?,?,?,?,?,?)',
                      ('Generated '+role,email,hash_password(password),db_role,now(),encrypt(settings,secret) if secret else None,now()))
    credentials[role]={'id':uid,'email':email,'password':password,'secret':secret}
# Prepare a fully real case in the disposable browser database. It is separate
# from the buyer/seller accounts used by the onboarding and empty-state scenarios.
case_factory=Factory(app)
case_seller=case_factory.seller()
case_buyer=case_factory.user()
case_product=case_factory.product(case_seller)
case_factory.cart(case_buyer,case_product)
checkout,_,_=case_factory.checkout(case_buyer)
if checkout.status_code!=201:
    raise RuntimeError('Could not prepare browser case shipment: '+checkout.text)
case_shipment=checkout.json()['order']['shipments'][0]
previous='new'
for status in ('confirmed','preparing','shipped'):
    changed=case_seller['client'].patch(
        f"/api/seller/orders/{case_shipment['id']}/status",
        json={'status':status,'expected_status':previous},
    )
    if changed.status_code!=200:
        raise RuntimeError('Could not prepare browser shipment status: '+changed.text)
    previous=status
case_open=case_buyer['client'].post('/api/cases',json={
    'seller_order_id':case_shipment['id'],
    'reason':'not_received',
    'description':'The shipment has not arrived. This generated browser test case includes a private evidence image.',
})
if case_open.status_code!=201:
    raise RuntimeError('Could not prepare browser shipment case: '+case_open.text)
credentials['case_buyer']={
    'id':case_buyer['id'],'email':case_buyer['email'],'password':case_buyer['password'],'secret':None
}
credentials['case_seller']={
    'id':case_seller['id'],'email':case_seller['email'],'password':case_seller['password'],'secret':None
}
credentials['case_id']=case_open.json()['case']['id']
p=folder/'credentials.json'
p.write_text(json.dumps(credentials));p.chmod(0o600)
Image.new('RGB',(200,160),'#7c9379').save(folder/'product.png')
Image.new('RGB',(128,96),(35,85,135)).save(folder/'evidence.png')
if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='0.0.0.0',port=8011,access_log=False)
