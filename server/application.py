"""SHAM’A marketplace — one same-origin web/API server, no simulated payments."""
import hmac
import html
import logging
from pathlib import Path
import secrets
import sqlite3
from typing import Literal
from urllib.parse import urlsplit
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .config import ROOT, Settings
from .database import Database
from .errors import error_body, fail, language
from .security import optional_user
from .seed import initialize_catalog
from .commerce import PRODUCT_SQL, PUBLIC_WHERE
from .routes import admin, auth, catalog, orders, seller

log = logging.getLogger('shamaa')
STATIC = ROOT / 'static'


class RequestSizeLimit:
    def __init__(self, app, limit=6*1024*1024):
        self.app,self.limit = app,limit

    async def __call__(self, scope, receive, send):
        if scope['type']!='http' or scope['method'] in ('GET','HEAD','OPTIONS'):
            return await self.app(scope,receive,send)
        chunks,total = [],0
        while True:
            event = await receive()
            if event['type']=='http.disconnect':
                return
            chunk = event.get('body',b'')
            total += len(chunk)
            if total>self.limit:
                return await JSONResponse(error_body('media_size',language(Request(scope))),status_code=413)(scope,receive,send)
            chunks.append(chunk)
            if not event.get('more_body'):
                break
        supplied = False
        async def replay():
            nonlocal supplied
            if not supplied:
                supplied = True
                return {'type':'http.request','body':b''.join(chunks),'more_body':False}
            return await receive()
        await self.app(scope,replay,send)


def create_app(settings=None):
    s = settings or Settings()
    s.prepare()
    db = Database(s.db_path,s.sqlite_journal_mode)
    db.initialize()
    with db.read() as c:
        if s.production and (c.execute('SELECT COUNT(*) FROM stores WHERE is_demo=1').fetchone()[0]
                             or c.execute('SELECT COUNT(*) FROM orders WHERE is_demo=1').fetchone()[0]):
            raise RuntimeError('Illustrative catalog or development orders exist. Use a clean production database.')
    initialize_catalog(db,s)
    app = FastAPI(title='SHAM’A · شمعة',version='1.0.0',docs_url=None,redoc_url=None,openapi_url=None)
    app.state.settings,app.state.db = s,db

    @app.middleware('http')
    async def security_headers(request, call_next):
        if request.method not in ('GET','HEAD','OPTIONS') and request.url.path.startswith('/api/'):
            origin = request.headers.get('origin')
            host = request.headers.get('host','')
            allowed = {s.public_url, str(request.base_url).rstrip('/')}
            if not s.production and (host.split(':')[0].endswith('.e2b.app')):
                allowed.add('https://'+host)
            if request.headers.get('x-requested-with')!='shamaa' or (origin and origin not in allowed):
                response = JSONResponse(error_body('csrf',language(request)),status_code=403)
            else:
                user = optional_user(request)
                if user and not hmac.compare_digest(request.headers.get('x-csrf-token',''),user['csrf_token']):
                    response = JSONResponse(error_body('csrf',language(request)),status_code=403)
                else:
                    response = await call_next(request)
        else:
            response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
        # Preview is intentionally embeddable; production is limited to its own origin.
        frame = "'self'" if s.production else "'self' https://*.arena.ai https://arena.ai https://*.e2b.app"
        response.headers['Content-Security-Policy'] = ("default-src 'self'; script-src 'self'; style-src 'self'; "
          "img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; "
          "form-action 'self'; frame-ancestors "+frame)
        path = request.url.path
        public_api = path in ('/api/bootstrap','/api/products','/api/stores') or path.startswith(('/api/products/','/api/stores/'))
        if path.startswith('/api/'):
            response.headers['Cache-Control'] = 'public, max-age=0, must-revalidate' if public_api and response.status_code==200 else 'no-store'
            if not public_api or response.status_code!=200:
                response.headers['Vary'] = 'X-Language, Accept-Language'
        elif response.headers.get('content-type','').startswith('text/html'):
            response.headers['Cache-Control'] = 'no-store' if path in ('/account','/seller','/admin','/orders','/cart','/checkout','/verify-email','/reset-password','/favorites') else 'no-cache'
            response.headers['Vary'] = 'Cookie, Accept-Language'
        if s.production:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        else:
            response.headers['X-Robots-Tag'] = 'noindex, nofollow'
        return response

    # Order: trusted-host and body cap also apply before expensive body parsing.
    app.add_middleware(RequestSizeLimit)
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=s.allowed_hosts)
    app.mount('/static',StaticFiles(directory=STATIC),name='static')
    for router in (auth.router,catalog.router,seller.router,orders.router,admin.router):
        app.include_router(router)

    @app.exception_handler(HTTPException)
    async def http_error(request,exc):
        code = exc.detail.get('code','invalid_input') if isinstance(exc.detail,dict) else 'not_found' if exc.status_code==404 else 'invalid_input'
        return JSONResponse(error_body(code,language(request)),status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request,exc):
        # Do not echo inputs: passwords, email addresses and addresses can appear in validation errors.
        fields = list(dict.fromkeys('.'.join(str(x) for x in e['loc'][1:]) for e in exc.errors()))
        return JSONResponse(error_body('invalid_input',language(request),fields=fields),status_code=422)

    @app.exception_handler(sqlite3.OperationalError)
    async def database_error(request,exc):
        log.error('Database operation failed (%s)',type(exc).__name__)
        return JSONResponse(error_body('database_busy',language(request)),status_code=503)

    @app.exception_handler(Exception)
    async def unexpected(request,exc):
        # No request body, query string, credentials or personal data in logs.
        event = secrets.token_hex(6)
        log.error('Unexpected application error id=%s type=%s',event,type(exc).__name__)
        return JSONResponse(error_body('server_error',language(request),event_id=event),status_code=500)

    @app.api_route('/api/{path:path}', methods=['GET','POST','PUT','PATCH','DELETE'])
    def unknown_api(path: str):
        fail('not_found',404)

    @app.get('/healthz')
    def health():
        with db.read() as c:
            if c.execute('PRAGMA user_version').fetchone()[0]!=1 or not c.execute("SELECT 1 FROM settings WHERE key='commission_bps'").fetchone():
                fail('database_busy',503)
        return {'ok':True,'version':'1.0.0','database':'ready'}

    @app.get('/media/{media_id}/{size}.webp')
    def media(media_id: int,size: Literal['480','960'],request: Request,user=Depends(optional_user)):
        with db.read() as c:
            row = c.execute('SELECT * FROM media WHERE id=?',(media_id,)).fetchone()
            if not row:
                fail('not_found',404)
            public = c.execute('SELECT 1 FROM ('+PRODUCT_SQL+' WHERE p.media_id=? AND '+PUBLIC_WHERE+') LIMIT 1',(media_id,)).fetchone()
            private_access = bool(user) and (row['owner_id']==user['id'] or user['role']=='admin')
            if not public and not private_access:
                fail('not_found',404)
            path = s.data_dir/'media'/f"{row['storage_key']}-{size}.webp"
            if not path.is_file():
                fail('not_found',404)
        return FileResponse(path,media_type='image/webp',headers={'Cache-Control':'public, max-age=3600' if public else 'no-store'})

    @app.get('/sw.js')
    def sw():
        return FileResponse(STATIC/'sw.js',media_type='application/javascript',headers={'Cache-Control':'no-cache','Service-Worker-Allowed':'/'})

    @app.get('/manifest.webmanifest')
    def manifest():
        return FileResponse(STATIC/'manifest.webmanifest',media_type='application/manifest+json')

    @app.get('/offline.html')
    def offline():
        return FileResponse(STATIC/'offline.html',media_type='text/html')

    @app.get('/robots.txt')
    def robots():
        text = 'User-agent: *\nDisallow: /\n' if not s.production else ('User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /admin\nDisallow: /seller\nDisallow: /account\nDisallow: /orders\nDisallow: /cart\nDisallow: /checkout\nDisallow: /verify-email\nDisallow: /reset-password\nSitemap: '+s.public_url+'/sitemap.xml\n')
        return Response(text,media_type='text/plain')

    @app.get('/sitemap.xml')
    def sitemap():
        urls = [s.public_url+'/',s.public_url+'/stores']
        with db.read() as c:
            urls += [s.public_url+f"/product/{r['id']}" for r in c.execute(PRODUCT_SQL+' WHERE '+PUBLIC_WHERE)]
            urls += [s.public_url+'/store/'+r['slug'] for r in c.execute("SELECT s.slug FROM stores s JOIN users u ON u.id=s.owner_id WHERE s.status='active' AND u.active=1")]
        xml = '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'+''.join('<url><loc>'+html.escape(u)+'</loc></url>' for u in urls)+'</urlset>'
        return Response(xml,media_type='application/xml')

    @app.get('/')
    @app.get('/search')
    @app.get('/stores')
    @app.get('/store/{slug}')
    @app.get('/product/{product_id}')
    @app.get('/cart')
    @app.get('/checkout')
    @app.get('/orders')
    @app.get('/account')
    @app.get('/seller')
    @app.get('/admin')
    @app.get('/help')
    @app.get('/favorites')
    @app.get('/verify-email')
    @app.get('/reset-password')
    def index(request: Request,slug: str='',product_id: str=''):
        lang = request.query_params.get('lang') or request.cookies.get('shamaa_lang') or ('en' if request.headers.get('accept-language','').startswith('en') else 'ar')
        lang = 'en' if lang=='en' else 'ar'
        title = 'شمعة | اختيارات تشبهك، من متاجر محلية' if lang=='ar' else 'SHAM’A | Thoughtful finds from local stores'
        description = 'اكتشف متاجر ومنتجات محلية، وتسوّق بالدينار الأردني مع الدفع عند الاستلام.' if lang=='ar' else 'Discover local stores and thoughtful products. Shop in JOD with cash on delivery.'
        status = 200
        if product_id or slug:
            with db.read() as c:
                if product_id:
                    row = c.execute(PRODUCT_SQL+' WHERE p.id=? AND '+PUBLIC_WHERE,(product_id,)).fetchone()
                    if row:
                        title,description = row['title_'+lang]+' | شمعة SHAM’A',row['description_'+lang][:180]
                else:
                    row = c.execute("SELECT s.* FROM stores s JOIN users u ON u.id=s.owner_id WHERE s.slug=? AND s.status='active' AND u.active=1",(slug,)).fetchone()
                    if row:
                        title,description = row['name_'+lang]+' | شمعة SHAM’A',row['description_'+lang][:180]
                if not row:
                    status,title = 404,'غير موجود | Not found · SHAM’A'
        private = request.url.path in ('/account','/seller','/admin','/orders','/cart','/checkout','/verify-email','/reset-password','/favorites')
        meta = '<meta name="robots" content="noindex,nofollow">' if private else ''
        meta += f'<link rel="canonical" href="{html.escape(s.public_url+request.url.path,quote=True)}">'
        meta += f'<meta property="og:title" content="{html.escape(title,quote=True)}"><meta property="og:description" content="{html.escape(description,quote=True)}">'
        page = (STATIC/'index.html').read_text(encoding='utf-8').replace('__TITLE__',html.escape(title)).replace('__DESCRIPTION__',html.escape(description,quote=True)).replace('<!-- META -->',meta)
        page = page.replace('<html lang="ar" dir="rtl">',f'<html lang="{lang}" dir="{"rtl" if lang=="ar" else "ltr"}">')
        return HTMLResponse(page,status_code=status)

    @app.get('/{path:path}')
    def not_found(path: str):
        return HTMLResponse((STATIC/'404.html').read_text(encoding='utf-8'),status_code=404)
    return app
