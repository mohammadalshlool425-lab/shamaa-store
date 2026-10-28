import secrets
import sqlite3
from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse
from ..database import now
from ..errors import fail
from .. import mail, schemas
from ..security import (COOKIE, DUMMY_HASH, audit, digest, hash_password, optional_user, public_user,
                        require_user, throttle, verify_password, verify_totp)

router = APIRouter(prefix='/api/auth', tags=['Accounts'])


def email_token(c, request, user, purpose):
    token = secrets.token_urlsafe(32)
    c.execute('UPDATE auth_tokens SET used_at=? WHERE user_id=? AND purpose=? AND used_at IS NULL',(now(),user['id'],purpose))
    c.execute('INSERT INTO auth_tokens VALUES(?,?,?,?,NULL)',
              (digest(token),user['id'],purpose,now()+(86400 if purpose=='verify' else 1800)))
    path = 'verify-email' if purpose=='verify' else 'reset-password'
    link = f'{request.app.state.settings.public_url}/{path}#token={token}'
    ar = 'أكّد بريدك الإلكتروني' if purpose=='verify' else 'استعادة كلمة المرور'
    en = 'Verify your email' if purpose=='verify' else 'Reset your password'
    body = (f'مرحبًا {user["name"]}،\n{ar} على شمعة عبر الرابط التالي:\n{link}\nإذا لم تطلب هذا الإجراء فتجاهل الرسالة.'
            if user['locale']=='ar' else f'Hello {user["name"]},\n{en} on SHAM’A using this link:\n{link}\nIf you did not request this, ignore this message.')
    mail.queue(c, request.app.state.settings, user['email'], f'شمعة · {ar if user["locale"]=="ar" else en}',body,user['locale'])


def start_session(c, request, user):
    raw, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    ip = digest(request.client.host if request.client else 'local')
    agent = request.headers.get('user-agent','Unknown device')[:300]
    previous = c.execute('SELECT 1 FROM sessions WHERE user_id=? LIMIT 1',(user['id'],)).fetchone()
    known = c.execute('SELECT 1 FROM sessions WHERE user_id=? AND ip_hash=? AND user_agent=? LIMIT 1',(user['id'],ip,agent)).fetchone()
    if previous and not known:
        mail.notify(c,request.app.state.settings,user['id'],'security',
                    'تسجيل دخول من جهاز أو شبكة جديدة. راجع جلسات حسابك إذا لم تكن أنت.',
                    'A new device or network signed in. Review your sessions if this was not you.')
    c.execute('DELETE FROM sessions WHERE expires_at<?',(now(),))
    c.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?)',
              (digest(raw),user['id'],csrf,now(),now()+request.app.state.settings.session_seconds,ip,agent,now() if user['role']=='admin' else None))
    response = JSONResponse({'user':public_user(user),'csrf_token':csrf})
    response.set_cookie(COOKIE,raw,httponly=True,secure=request.app.state.settings.secure_cookies,
                        samesite='lax',max_age=request.app.state.settings.session_seconds,path='/')
    return response


def schedule(tasks, request):
    tasks.add_task(mail.deliver_pending,request.app.state.db,request.app.state.settings)


@router.post('/register', status_code=201)
def register(body: schemas.Register, request: Request, tasks: BackgroundTasks):
    throttle(request,'register',5,3600)
    hashed = hash_password(body.password)
    try:
        with request.app.state.db.transaction() as c:
            cur = c.execute('INSERT INTO users(name,email,password_hash,locale,created_at) VALUES(?,?,?,?,?)',
                            (body.name, str(body.email).lower(),hashed,body.locale,now()))
            u = c.execute('SELECT * FROM users WHERE id=?',(cur.lastrowid,)).fetchone()
            email_token(c,request,u,'verify')
            audit(c,u['id'],'register','user',u['id'])
            response = start_session(c,request,u)
            response.status_code = 201
    except sqlite3.IntegrityError:
        fail('email_exists',409)
    schedule(tasks,request)
    return response


@router.post('/login')
def login(body: schemas.Login, request: Request, tasks: BackgroundTasks):
    throttle(request,'login_ip',30,900)
    throttle(request,'login_email',8,900, str(body.email).lower())
    with request.app.state.db.read() as c:
        row = c.execute('SELECT * FROM users WHERE email=?',(str(body.email).lower(),)).fetchone()
        u = dict(row) if row else None
    valid = verify_password(body.password, u['password_hash'] if u else DUMMY_HASH)
    if not valid or not u or not u['active']:
        fail('invalid_login',401)
    with request.app.state.db.transaction() as c:
        # Re-read after the expensive hash, so suspension/password reset cannot race login.
        fresh = c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone()
        if not fresh['active'] or fresh['password_hash'] != u['password_hash']:
            fail('invalid_login',401)
        if u['role']=='admin' and not verify_totp(c,fresh,body.otp,request.app.state.settings):
            fail('invalid_login',401)
        response = start_session(c,request,fresh)
        audit(c,u['id'],'login','user',u['id'])
    schedule(tasks,request)
    return response


@router.get('/me')
def me(user=Depends(optional_user)):
    return {'user':public_user(user),'csrf_token':user['csrf_token'] if user else None}


@router.post('/logout')
def logout(request: Request, user=Depends(require_user)):
    with request.app.state.db.transaction() as c:
        c.execute('DELETE FROM sessions WHERE token_hash=?',(user['session_id'],))
    response = JSONResponse({'ok':True})
    response.delete_cookie(COOKIE,path='/')
    return response


@router.post('/verify')
def verify(body: schemas.TokenInput, request: Request):
    throttle(request,'verify',20,300)
    with request.app.state.db.transaction() as c:
        token = c.execute("SELECT * FROM auth_tokens WHERE token_hash=? AND purpose='verify' AND used_at IS NULL AND expires_at>?",(digest(body.token),now())).fetchone()
        if not token:
            fail('invalid_token',400)
        c.execute('UPDATE users SET email_verified_at=? WHERE id=?',(now(),token['user_id']))
        c.execute('UPDATE auth_tokens SET used_at=? WHERE token_hash=?',(now(),token['token_hash']))
        audit(c,token['user_id'],'email_verified','user',token['user_id'])
    return {'ok':True}


@router.post('/resend-verification')
def resend(request: Request, tasks: BackgroundTasks, user=Depends(require_user)):
    throttle(request,'resend',3,3600,str(user['id']))
    if not user['email_verified_at']:
        with request.app.state.db.transaction() as c:
            email_token(c,request,user,'verify')
        schedule(tasks,request)
    return {'ok':True}


@router.post('/forgot-password')
def forgot(body: schemas.EmailInput, request: Request, tasks: BackgroundTasks):
    throttle(request,'forgot',5,3600)
    throttle(request,'forgot_email',3,3600,str(body.email).lower())
    with request.app.state.db.transaction() as c:
        u = c.execute('SELECT * FROM users WHERE email=? AND active=1',(str(body.email).lower(),)).fetchone()
        if u:
            email_token(c,request,u,'reset')
    schedule(tasks,request)
    return {'ok':True}  # deliberately does not reveal whether an account exists


@router.post('/reset-password')
def reset(body: schemas.Reset, request: Request):
    throttle(request,'reset',10,3600)
    hashed = hash_password(body.password)
    with request.app.state.db.transaction() as c:
        token = c.execute("SELECT * FROM auth_tokens WHERE token_hash=? AND purpose='reset' AND used_at IS NULL AND expires_at>?",(digest(body.token),now())).fetchone()
        if not token:
            fail('invalid_token',400)
        c.execute('UPDATE users SET password_hash=? WHERE id=?',(hashed,token['user_id']))
        c.execute('UPDATE auth_tokens SET used_at=? WHERE token_hash=?',(now(),token['token_hash']))
        c.execute('DELETE FROM sessions WHERE user_id=?',(token['user_id'],))
        audit(c,token['user_id'],'password_reset','user',token['user_id'])
    r = JSONResponse({'ok':True})
    r.delete_cookie(COOKIE,path='/')
    return r


@router.get('/sessions')
def sessions(request: Request,user=Depends(require_user)):
    with request.app.state.db.read() as c:
        rows = c.execute('SELECT token_hash,created_at,expires_at,user_agent FROM sessions WHERE user_id=? AND expires_at>? ORDER BY created_at DESC',(user['id'],now())).fetchall()
    return {'sessions':[{'id':r['token_hash'],'created_at':r['created_at'],'expires_at':r['expires_at'],
                         'device':r['user_agent'],'current':r['token_hash']==user['session_id']} for r in rows]}


@router.delete('/sessions/{session_id}')
def revoke_session(session_id: str,request: Request,user=Depends(require_user)):
    with request.app.state.db.transaction() as c:
        c.execute('DELETE FROM sessions WHERE token_hash=? AND user_id=?',(session_id,user['id']))
        audit(c,user['id'],'session_revoked','user',user['id'])
    return {'ok':True}


@router.post('/reauth')
def reauth(body: schemas.Reauth,request: Request,user=Depends(require_user)):
    throttle(request,'reauth',5,300,str(user['id']))
    if user['role']!='admin' or not verify_password(body.password,user['password_hash']):
        fail('invalid_login',401)
    with request.app.state.db.transaction() as c:
        fresh = c.execute('SELECT * FROM users WHERE id=?',(user['id'],)).fetchone()
        if not verify_totp(c,fresh,body.otp,request.app.state.settings):
            fail('invalid_login',401)
        c.execute('UPDATE sessions SET mfa_at=? WHERE token_hash=?',(now(),user['session_id']))
    return {'ok':True}
