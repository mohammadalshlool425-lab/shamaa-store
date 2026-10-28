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
from server.database import now
from server.security import encrypt, hash_password

transport=os.getenv('SHAMAA_TEST_TRANSPORT','asgi')
if transport not in ('asgi','wsgi'):
    raise RuntimeError('SHAMAA_TEST_TRANSPORT must be asgi or wsgi')
folder=ROOT/'data'/'browser-tests'
if folder.exists():
    shutil.rmtree(folder) # this exact, dedicated directory only
folder.mkdir(parents=True,mode=0o700)
settings=Settings(env='development',db_path=folder/'marketplace.sqlite3',data_dir=folder,
                  demo=True,email_mode='file',encryption_key='',
                  sqlite_journal_mode='DELETE' if transport=='wsgi' else 'WAL',
                  public_url='http://127.0.0.1:8011',allowed_hosts=['127.0.0.1','localhost','*.e2b.app'])
app=create_app(settings)
credentials={}
for role in ('buyer','seller','admin'):
    password=secrets.token_urlsafe(24)+'a9'
    secret=pyotp.random_base32() if role=='admin' else None
    email=role+'-'+secrets.token_hex(4)+'@example.com'
    # The seller starts as a verified buyer, then applies for a store through the UI.
    db_role='buyer' if role=='seller' else role
    with app.state.db.transaction() as c:
        uid=c.execute('INSERT INTO users(name,email,password_hash,role,email_verified_at,mfa_secret,created_at) VALUES(?,?,?,?,?,?,?)',
                      ('Generated '+role,email,hash_password(password),db_role,now(),encrypt(settings,secret) if secret else None,now())).lastrowid
    credentials[role]={'id':uid,'email':email,'password':password,'secret':secret}
p=folder/'credentials.json'
p.write_text(json.dumps(credentials));p.chmod(0o600)
Image.new('RGB',(200,160),'#7c9379').save(folder/'product.png')
if __name__=='__main__':
    if transport=='wsgi':
        from socketserver import ThreadingMixIn
        from wsgiref.simple_server import WSGIServer,WSGIRequestHandler,make_server
        from server.wsgi import WSGIApplication
        class Server(ThreadingMixIn,WSGIServer):
            daemon_threads=True
            request_queue_size=128
        class QuietHandler(WSGIRequestHandler):
            def log_message(self,*args):
                pass # URLs may contain legacy verification tokens: do not log them.
        with make_server('0.0.0.0',8011,WSGIApplication(app),
                         server_class=Server,handler_class=QuietHandler) as server:
            server.serve_forever()
    else:
        import uvicorn
        uvicorn.run(app,host='0.0.0.0',port=8011,access_log=False)
