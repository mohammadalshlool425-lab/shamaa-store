from dataclasses import replace
import secrets
from unittest.mock import MagicMock
import pyotp
import pytest
from cryptography.fernet import Fernet
from server import cli,mail
from server.database import now
from server.application import create_app
from server.security import decrypt,verify_password


def test_cli_create_admin_with_real_mfa_confirmation(factory,monkeypatch,capsys):
    secret=pyotp.random_base32();password=secrets.token_urlsafe(22)+'a9'
    answers=iter(['Generated operator','operator@example.com',pyotp.TOTP(secret).now()])
    monkeypatch.setattr(cli,'Settings',lambda:factory.settings)
    monkeypatch.setattr(cli.pyotp,'random_base32',lambda:secret)
    monkeypatch.setattr('builtins.input',lambda _:next(answers))
    monkeypatch.setattr(cli.getpass,'getpass',lambda _:password)
    monkeypatch.setattr('sys.argv',['shamaa','create-admin'])
    cli.main()
    assert not (factory.settings.data_dir/'admin-mfa-setup.png').exists()
    with factory.db.read() as c:
        user=c.execute("SELECT * FROM users WHERE role='admin'").fetchone()
        assert user and verify_password(password,user['password_hash'])
        assert user['mfa_secret']!=secret and decrypt(factory.settings,user['mfa_secret'])==secret
    capsys.readouterr() # setup output is private and must not appear in test logs


def test_cli_rejects_wrong_mfa_without_creating_account(factory,monkeypatch,capsys):
    secret=pyotp.random_base32();code='000000'
    if pyotp.TOTP(secret).now()==code:code='111111'
    answers=iter(['Generated operator','operator@example.com',code])
    monkeypatch.setattr(cli,'Settings',lambda:factory.settings)
    monkeypatch.setattr(cli.pyotp,'random_base32',lambda:secret)
    monkeypatch.setattr('builtins.input',lambda _:next(answers))
    password=secrets.token_urlsafe(22)+'a9'
    monkeypatch.setattr(cli.getpass,'getpass',lambda _:password)
    monkeypatch.setattr('sys.argv',['shamaa','create-admin'])
    with pytest.raises(SystemExit):cli.main()
    with factory.db.read() as c:assert c.execute('SELECT COUNT(*) FROM users').fetchone()[0]==0
    assert not (factory.settings.data_dir/'admin-mfa-setup.png').exists()
    capsys.readouterr()


def test_cli_backup_and_restore_commands(factory,monkeypatch,capsys,tmp_path):
    monkeypatch.setattr(cli,'Settings',lambda:factory.settings)
    monkeypatch.setenv('SHAMAA_BACKUP_KEY',Fernet.generate_key().decode())
    archive=tmp_path/'cli-backup.shama'
    monkeypatch.setattr('sys.argv',['shamaa','backup','--output',str(archive)])
    cli.main();assert archive.is_file()
    monkeypatch.setattr('sys.argv',['shamaa','backup-rotate','--directory',str(tmp_path/'rotated'),'--keep-days','14'])
    cli.main();assert len(list((tmp_path/'rotated').glob('*.shama')))==1
    restored=tmp_path/'cli-restored'
    monkeypatch.setattr('sys.argv',['shamaa','restore','--input',str(archive),'--to',str(restored)])
    cli.main();assert (restored/'marketplace.sqlite3').is_file()
    capsys.readouterr()


def test_cli_key_init_and_mail_status(factory,monkeypatch,capsys):
    monkeypatch.setattr('sys.argv',['shamaa','generate-key']);cli.main()
    key=capsys.readouterr().out.strip();Fernet(key.encode())
    monkeypatch.setattr(cli,'Settings',lambda:factory.settings)
    for command in ['init','mail-worker','mail-status']:
        monkeypatch.setattr('sys.argv',['shamaa',command]+(['--once'] if command=='mail-worker' else []))
        cli.main();capsys.readouterr()


def test_smtp_transport_uses_tls_and_retries_without_leaking_provider_secrets(factory,monkeypatch):
    settings=replace(factory.settings,email_mode='smtp',smtp_host='smtp.example.com',smtp_user='generated',smtp_password=secrets.token_urlsafe(20))
    smtp=MagicMock();smtp.__enter__.return_value=smtp
    monkeypatch.setattr(mail.smtplib,'SMTP',lambda *a,**k:smtp)
    with factory.db.transaction() as c:mail.queue(c,settings,'generated@example.com','Subject','Private test message','en')
    mail.deliver_pending(factory.db,settings)
    smtp.starttls.assert_called_once();smtp.login.assert_called_once();smtp.send_message.assert_called_once()
    with factory.db.read() as c:assert c.execute('SELECT status FROM mail_outbox').fetchone()[0]=='sent'
    smtp.send_message.side_effect=RuntimeError(settings.smtp_password)
    with factory.db.transaction() as c:mail.queue(c,settings,'generated@example.com','Subject','Retry test','en')
    mail.deliver_pending(factory.db,settings)
    with factory.db.read() as c:
        row=c.execute('SELECT * FROM mail_outbox ORDER BY id DESC LIMIT 1').fetchone()
        assert row['status']=='failed' and row['attempts']==1 and row['available_at']>now()
        assert row['last_error']=='RuntimeError' and settings.smtp_password not in row['last_error']


def test_opt_in_demo_catalog_and_production_guard(factory):
    app=create_app(replace(factory.settings,demo=True))
    with app.state.db.read() as c:
        assert c.execute('SELECT COUNT(*) FROM products').fetchone()[0]==8
        assert c.execute('SELECT COUNT(*) FROM stores WHERE is_demo=1').fetchone()[0]==3
        assert not c.execute("SELECT 1 FROM users WHERE role='admin'").fetchone()
    public=factory.client().get('/api/bootstrap').json()
    assert 'credentials' not in public and 'password' not in str(public)
    assert all('commission_bps' not in cat for cat in public['categories'])
    production=replace(factory.settings,env='production',demo=False,email_mode='smtp',smtp_host='mail.example.com',public_url='https://testserver',allowed_hosts=['testserver'])
    with pytest.raises(RuntimeError):create_app(production)


def test_edit_store_requires_review_for_identity_but_not_shipping(factory):
    seller=factory.seller();client=seller['client']
    store=client.get('/api/seller/store').json()['store']
    body={k:store[k] for k in ['slug','name_ar','name_en','description_ar','description_en','city','accent','shipping_fils','delivery_days']}
    body['shipping_fils']=4000
    response=client.put('/api/seller/store',json=body)
    assert response.status_code==200 and response.json()['store']['status']=='active'
    body['name_ar']='اسم جديد للمتجر'
    response=client.put('/api/seller/store',json=body)
    assert response.json()['store']['status']=='pending'
    assert factory.client().get('/api/stores/'+store['slug']).status_code==404


def test_edit_product_pause_review_and_revision_control(factory):
    seller=factory.seller();p=factory.product(seller)
    current=seller['client'].get('/api/seller/products').json()['products'][0]
    body={k:current[k] for k in ['category_id','title_ar','title_en','description_ar','description_en','price_fils','stock','sku','media_id']}
    body.update(status='paused',expected_version=current['version'])
    r=seller['client'].put('/api/seller/products/'+str(p['id']),json=body)
    assert r.status_code==200 and r.json()['product']['status']=='paused'
    assert factory.client().get('/api/products/'+str(p['id'])).status_code==404
    assert seller['client'].put('/api/seller/products/'+str(p['id']),json=body).status_code==409
    body['expected_version']=r.json()['product']['version'];body['status']='pending';body['stock']=12
    assert seller['client'].put('/api/seller/products/'+str(p['id']),json=body).status_code==200
    movements=seller['client'].get('/api/seller/inventory').json()['movements']
    assert movements[0]['reason']=='adjustment' and movements[0]['delta']==2
    assert seller['client'].get('/api/seller/summary').json()['summary']['orders']==0


def test_admin_commission_routes_are_validated_and_audited(factory):
    admin=factory.user('admin')['client'];seller=factory.seller()
    assert admin.put('/api/admin/commissions/store/'+str(seller['store_id']),json={'commission_bps':750}).status_code==200
    assert admin.put('/api/admin/commissions/category/home',json={'commission_bps':900}).status_code==200
    saved=admin.get('/api/admin/settings').json()['category_commissions']
    assert next(c for c in saved if c['id']=='home')['commission_bps']==900
    assert admin.put('/api/admin/commissions/category/home',json={'commission_bps':None}).status_code==200
    assert admin.put('/api/admin/commissions/arbitrary/1',json={'commission_bps':500}).status_code==404
    assert admin.put('/api/admin/commissions/store/999',json={'commission_bps':500}).status_code==404
    assert admin.put('/api/admin/commissions/category/home',json={'commission_bps':5001}).status_code==422
    assert admin.get('/api/admin/summary').json()['users']==2
    assert admin.get('/api/admin/ledger').json()['entries']==[]
    assert len(admin.get('/api/admin/audit').json()['events'])>=3


def test_importing_factory_never_initializes_the_live_database(tmp_path):
    import os,subprocess,sys
    env={**os.environ,'SHAMAA_DATA_DIR':str(tmp_path/'must-not-exist'),
         'SHAMAA_DB':str(tmp_path/'must-not-exist'/'untouched.sqlite3'),'SHAMAA_DEMO_DATA':'1'}
    result=subprocess.run([sys.executable,'-c','from server.application import create_app; assert callable(create_app)'],env=env,capture_output=True)
    assert result.returncode==0 and not (tmp_path/'must-not-exist').exists()


def test_private_development_mail_preview_is_never_available_for_smtp(factory,monkeypatch,capsys):
    monkeypatch.setattr(cli,'Settings',lambda:factory.settings)
    monkeypatch.setattr('sys.argv',['shamaa','dev-mail'])
    monkeypatch.setattr('builtins.input',lambda _:'generated@example.com')
    with pytest.raises(SystemExit):cli.main()
    with factory.db.transaction() as c:mail.queue(c,factory.settings,'generated@example.com','Subject','Generated private message','en')
    mail.deliver_pending(factory.db,factory.settings)
    cli.main();assert 'NOT DELIVERED' in capsys.readouterr().out
    smtp=replace(factory.settings,email_mode='smtp')
    monkeypatch.setattr(cli,'Settings',lambda:smtp)
    with pytest.raises(SystemExit):cli.main()
    capsys.readouterr()


def test_trial_orders_are_marked_and_cannot_be_promoted_to_production(factory):
    seller=factory.seller();product=factory.product(seller);buyer=factory.user()
    factory.cart(buyer,product);response,_,_=factory.checkout(buyer)
    assert response.status_code==201 and response.json()['order']['is_demo']==1
    settings=replace(factory.settings,env='production',demo=False,email_mode='smtp',smtp_host='mail.example.com',public_url='https://testserver',allowed_hosts=['testserver'])
    with pytest.raises(RuntimeError):create_app(settings)


def test_health_rejects_missing_schema_instead_of_reporting_an_empty_database_ready(factory):
    assert factory.client().get('/healthz').json()['database']=='ready'
    with factory.db.transaction() as c:c.execute('DROP TABLE settings')
    assert factory.client().get('/healthz').status_code==503


def test_cli_never_echoes_an_invalid_password(factory,monkeypatch,capsys):
    answers=iter(['Generated operator','operator@example.com'])
    password=secrets.token_hex(3)
    monkeypatch.setattr(cli,'Settings',lambda:factory.settings)
    monkeypatch.setattr('builtins.input',lambda _:next(answers))
    monkeypatch.setattr(cli.getpass,'getpass',lambda _:password)
    monkeypatch.setattr('sys.argv',['shamaa','create-admin'])
    with pytest.raises(SystemExit) as error:cli.main()
    assert password not in str(error.value)
    assert password not in capsys.readouterr().out
