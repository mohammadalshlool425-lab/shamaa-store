from email.parser import BytesParser
from email import policy
import re
import secrets
import pyotp
from server.database import now
from server.security import COOKIE, digest, verify_password


def message_token(settings,purpose):
    for p in sorted((settings.data_dir/'mail').glob('*.eml'),reverse=True):
        msg=BytesParser(policy=policy.default).parsebytes(p.read_bytes())
        text=msg.get_body(preferencelist=('plain',)).get_content()
        found=re.search(r'/'+purpose+r'#token=([A-Za-z0-9_-]+)',text)
        if found:return found.group(1)
    raise AssertionError('No matching private email')


def register(factory,email='new@example.com'):
    client=factory.client()
    password=secrets.token_urlsafe(24)+'a9'
    r=client.post('/api/auth/register',json={'name':'New account','email':email,'password':password})
    assert r.status_code==201,r.text
    client.headers['X-CSRF-Token']=r.json()['csrf_token']
    return client,r.json()['user'],password


def test_real_registration_hashing_and_email_verification(factory):
    client,user,password=register(factory)
    assert not user['email_verified_at']
    token=message_token(factory.settings,'verify-email')
    with factory.db.read() as c:
        row=c.execute('SELECT * FROM users WHERE id=?',(user['id'],)).fetchone()
        assert row['password_hash']!=password and verify_password(password,row['password_hash'])
        assert c.execute('SELECT token_hash FROM auth_tokens').fetchone()[0]==digest(token)
        assert c.execute('SELECT token_hash FROM sessions').fetchone()[0]!=client.cookies.get(COOKIE)
    # A GET link is only a page. It must not consume verification.
    assert client.get('/verify-email?token='+token).status_code==200
    assert not client.get('/api/auth/me').json()['user']['email_verified_at']
    assert client.post('/api/auth/verify',json={'token':token}).status_code==200
    assert client.get('/api/auth/me').json()['user']['email_verified_at']
    assert client.post('/api/auth/verify',json={'token':token}).status_code==400


def test_registration_rejects_privilege_injection(factory):
    client=factory.client()
    response=client.post('/api/auth/register',json={'name':'Malicious','email':'x@example.com','password':secrets.token_urlsafe(15)+'1a','role':'admin'})
    assert response.status_code==422
    assert 'password' not in response.text


def test_duplicate_email_case_insensitive(factory):
    register(factory)
    other=factory.client()
    r=other.post('/api/auth/register',json={'name':'Another','email':'NEW@example.com','password':secrets.token_urlsafe(15)+'1a'})
    assert r.status_code==409


def test_logout_revokes_server_session(factory):
    u=factory.user();client=u['client'];token=client.cookies.get(COOKIE)
    assert client.post('/api/auth/logout').status_code==200
    attacker=factory.client();attacker.cookies.set(COOKIE,token)
    assert attacker.get('/api/auth/me').json()['user'] is None


def test_expired_session_is_not_accepted(factory):
    u=factory.user()
    with factory.db.transaction() as c:c.execute('UPDATE sessions SET expires_at=?',(now()-1,))
    assert u['client'].get('/api/cart').status_code==401


def test_unverified_cannot_open_store(factory):
    u=factory.user(verified=False)
    r=u['client'].post('/api/seller/store',json={'slug':'new-store','name_ar':'متجر','name_en':'Store','city':'irbid'})
    assert r.status_code==403 and r.json()['code']=='verify_email'


def test_expired_and_resend_tokens(factory):
    client,user,_=register(factory)
    old=message_token(factory.settings,'verify-email')
    assert client.post('/api/auth/resend-verification').status_code==200
    assert client.post('/api/auth/verify',json={'token':old}).status_code==400
    new=message_token(factory.settings,'verify-email')
    with factory.db.transaction() as c:c.execute('UPDATE auth_tokens SET expires_at=? WHERE token_hash=?',(now()-1,digest(new)))
    assert client.post('/api/auth/verify',json={'token':new}).status_code==400


def test_password_reset_single_use_and_revokes_all_sessions(factory):
    client,user,password=register(factory)
    client.post('/api/auth/forgot-password',json={'email':user['email']})
    token=message_token(factory.settings,'reset-password')
    replacement=secrets.token_urlsafe(26)+'a8'
    assert client.post('/api/auth/reset-password',json={'token':token,'password':replacement}).status_code==200
    assert client.get('/api/auth/me').json()['user'] is None
    assert client.post('/api/auth/reset-password',json={'token':token,'password':replacement}).status_code==400
    assert client.post('/api/auth/login',json={'email':user['email'],'password':password}).status_code==401
    assert client.post('/api/auth/login',json={'email':user['email'],'password':replacement}).status_code==200


def test_reset_does_not_reveal_account_existence(factory):
    existing=factory.user()
    client=factory.client()
    a=client.post('/api/auth/forgot-password',json={'email':existing['email']})
    b=client.post('/api/auth/forgot-password',json={'email':'absent@example.com'})
    assert a.status_code==b.status_code==200 and a.json()==b.json()


def test_mfa_required_and_codes_cannot_be_replayed(factory):
    admin=factory.user('admin');client=factory.client()
    assert client.post('/api/auth/login',json={'email':admin['email'],'password':admin['password']}).status_code==401
    reused=pyotp.TOTP(admin['secret']).now()
    assert client.post('/api/auth/login',json={'email':admin['email'],'password':admin['password'],'otp':reused}).status_code==401
    code=pyotp.TOTP(admin['secret']).at(now()+30)
    assert client.post('/api/auth/login',json={'email':admin['email'],'password':admin['password'],'otp':code}).status_code==200


def test_new_device_security_notice_and_revocation_ownership(factory):
    user=factory.user();other=factory.user()
    client=factory.client();client.headers['User-Agent']='Different runtime browser'
    r=client.post('/api/auth/login',json={'email':user['email'],'password':user['password']})
    assert r.status_code==200;client.headers['X-CSRF-Token']=r.json()['csrf_token']
    assert any(n['kind']=='security' for n in client.get('/api/notifications').json()['notifications'])
    sessions=client.get('/api/auth/sessions').json()['sessions']
    victim=next(s['id'] for s in sessions if s['current'])
    assert other['client'].delete('/api/auth/sessions/'+victim).status_code==200
    assert client.get('/api/auth/me').json()['user']
    assert client.delete('/api/auth/sessions/'+victim).status_code==200
    assert client.get('/api/auth/me').json()['user'] is None


def test_rate_limit_is_persistent_across_app_instances(factory):
    user=factory.user();client=factory.client()
    results=[client.post('/api/auth/login',json={'email':user['email'],'password':'incorrect-generated-input'}).status_code for _ in range(9)]
    assert 429 in results
    from server.application import create_app
    from fastapi.testclient import TestClient
    restarted=TestClient(create_app(factory.settings))
    r=restarted.post('/api/auth/login',headers={'X-Requested-With':'shamaa'},json={'email':user['email'],'password':user['password']})
    assert r.status_code==429
