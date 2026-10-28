import base64
import hashlib
import hmac
import secrets
import time
from threading import BoundedSemaphore
from cryptography.fernet import Fernet
from fastapi import Depends, Request
import pyotp
from .database import now
from .errors import fail

COOKIE = 'shamaa_session'
# Bound memory-hard work within the one-worker deployment; not DDoS protection.
PASSWORD_JOBS = BoundedSemaphore(2)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def hash_password(password):
    salt = secrets.token_bytes(16)
    with PASSWORD_JOBS:
        result = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=32768, r=8, p=1, maxmem=64*1024*1024)
    return 'scrypt$' + base64.b64encode(salt).decode() + '$' + base64.b64encode(result).decode()


def verify_password(password, hashed):
    try:
        algorithm, salt, expected = hashed.split('$')
        if algorithm != 'scrypt':
            return False
        with PASSWORD_JOBS:
            actual = hashlib.scrypt(password.encode('utf-8'), salt=base64.b64decode(salt), n=32768, r=8, p=1, maxmem=64*1024*1024)
        return hmac.compare_digest(actual, base64.b64decode(expected))
    except (ValueError, TypeError):
        return False


# Equal-cost verification for unknown emails.
DUMMY_HASH = hash_password('unusable-random-' + secrets.token_urlsafe(24))


def encrypt(settings, value):
    return Fernet(settings.encryption_key.encode()).encrypt(value.encode()).decode()


def decrypt(settings, value):
    return Fernet(settings.encryption_key.encode()).decrypt(value.encode()).decode()


def verify_totp(conn, user, code, settings):
    if not user['mfa_secret'] or not code or len(code) != 6 or not code.isdigit():
        return False
    totp = pyotp.TOTP(decrypt(settings, user['mfa_secret']))
    step = now() // 30
    for offset in (0, -1, 1):
        counter = step + offset
        if counter > user['mfa_last_step'] and hmac.compare_digest(totp.at(counter * 30), code):
            conn.execute('UPDATE users SET mfa_last_step=? WHERE id=?', (counter, user['id']))
            return True
    return False


def throttle(request, scope, limit=10, window=300, identifier=None):
    ip = request.client.host if request.client else 'local'
    key = digest(scope + ':' + (identifier if identifier is not None else ip))
    t = now()
    with request.app.state.db.transaction() as c:
        c.execute('DELETE FROM rate_limits WHERE window_start<?', (t - 86400,))
        row = c.execute('SELECT * FROM rate_limits WHERE bucket=?', (key,)).fetchone()
        if row and row['window_start'] + window > t:
            if row['count'] >= limit:
                fail('rate_limit', 429)
            c.execute('UPDATE rate_limits SET count=count+1 WHERE bucket=?', (key,))
        else:
            c.execute('INSERT OR REPLACE INTO rate_limits VALUES(?,?,1)', (key, t))


def optional_user(request: Request):
    if hasattr(request.state, 'user'):
        return request.state.user
    token = request.cookies.get(COOKIE)
    user = None
    if token and len(token) < 256:
        with request.app.state.db.read() as c:
            row = c.execute('''SELECT u.*,s.token_hash AS session_id,s.csrf_token,s.mfa_at,s.created_at AS session_created
              FROM sessions s JOIN users u ON u.id=s.user_id
              WHERE s.token_hash=? AND s.expires_at>? AND u.active=1''', (digest(token), now())).fetchone()
            user = dict(row) if row else None
    request.state.user = user
    return user


def require_user(user=Depends(optional_user)):
    if user is None:
        fail('auth_required', 401)
    return user


def verified_user(user=Depends(require_user)):
    if not user['email_verified_at']:
        fail('verify_email', 403)
    return user


def seller_user(user=Depends(verified_user)):
    if user['role'] != 'seller':
        fail('forbidden', 403)
    return user


def admin_user(user=Depends(verified_user)):
    if user['role'] != 'admin' or not user['mfa_at']:
        fail('forbidden', 403)
    return user


def recent_admin(user=Depends(admin_user)):
    # Explicit password + OTP re-authentication is separately recorded on this session.
    if not user['mfa_at'] or now() - user['mfa_at'] > 600:
        fail('reauth', 403)
    return user


def public_user(user):
    if not user:
        return None
    return {k: user[k] for k in ('id','name','email','role','locale','email_verified_at')}


def audit(c, actor, action, entity, entity_id, detail=''):
    c.execute('INSERT INTO audit_log(actor_id,action,entity,entity_id,detail,created_at) VALUES(?,?,?,?,?,?)',
              (actor, action, entity, str(entity_id), detail, now()))
