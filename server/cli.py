"""Operations: no passwords or keys in command arguments, source code or Git."""
import argparse
import getpass
import os
from pathlib import Path
import time
import pyotp
import qrcode
from cryptography.fernet import Fernet
from email_validator import validate_email
from pydantic import ValidationError
from .backup import create_backup, restore_backup, rotate_backups
from .config import Settings
from .database import Database, now
from .mail import deliver_pending
from .schemas import Register
from .security import audit, encrypt, hash_password
from .seed import initialize_catalog


def setup():
    s = Settings()
    s.prepare()
    db = Database(s.db_path,s.sqlite_journal_mode)
    db.initialize()
    initialize_catalog(db,s)
    return s,db


def main():
    parser = argparse.ArgumentParser(description='SHAM’A operational tools')
    commands = parser.add_subparsers(dest='command',required=True)
    commands.add_parser('generate-key',help='Print a new encryption key. Keep it private.')
    commands.add_parser('create-admin',help='Interactive creation with password and mandatory TOTP')
    commands.add_parser('init',help='Initialize schema and reference categories')
    mail = commands.add_parser('mail-worker',help='Deliver queued email with retry')
    mail.add_argument('--once',action='store_true')
    backup = commands.add_parser('backup',help='Encrypted database + media backup')
    backup.add_argument('--output',required=True)
    rotate=commands.add_parser('backup-rotate',help='Encrypted backup job, then bounded retention; suitable for a scheduler')
    rotate.add_argument('--directory',required=True)
    rotate.add_argument('--keep-days',type=int,default=14)
    restore = commands.add_parser('restore',help='Validate and restore into a new, offline directory')
    restore.add_argument('--input',required=True)
    restore.add_argument('--to',required=True)
    commands.add_parser('mail-status',help='Delivery status counts only; no message content')
    commands.add_parser('dev-mail',help='PRIVATE development-only preview of file email; never sends mail')
    args = parser.parse_args()
    if args.command=='generate-key':
        print(Fernet.generate_key().decode())
        return
    s,db = setup()
    if args.command=='init':
        print('Schema v1 and reference categories ready. No default administrator exists.')
    elif args.command=='create-admin':
        name = input('Administrator name: ').strip()
        email = validate_email(input('Email: ').strip(),check_deliverability=False).normalized.lower()
        with db.read() as c:
            if c.execute('SELECT 1 FROM users WHERE email=?',(email,)).fetchone():
                raise SystemExit('Email already exists. Use a separate administrator email; no roles were changed.')
        password = getpass.getpass('Password (10+ characters, letters and numbers): ')
        try:
            Register(name=name,email=email,password=password)
        except ValidationError:
            raise SystemExit('Invalid name/email/password. Password needs 10+ characters, letters and numbers; input is not logged.') from None
        if password!=getpass.getpass('Repeat password: '):
            raise SystemExit('Passwords do not match')
        secret = pyotp.random_base32()
        uri = pyotp.TOTP(secret).provisioning_uri(email,issuer_name="SHAM’A")
        print('PRIVATE: add this setup key to your authenticator app:',secret)
        image_path = s.data_dir/'admin-mfa-setup.png'
        try:
            qrcode.make(uri).save(image_path)
            image_path.chmod(0o600)
            print('Or open the private QR image:',image_path)
            code = input('Current 6-digit authenticator code: ').strip()
            if not pyotp.TOTP(secret).verify(code):
                raise SystemExit('Invalid code; account was not created')
            hashed = hash_password(password)
            with db.transaction() as c:
                uid = c.execute("INSERT INTO users(name,email,password_hash,role,email_verified_at,mfa_secret,created_at) VALUES(?,?,?,'admin',?,?,?)",
                                (name,email,hashed,now(),encrypt(s,secret),now())).lastrowid
                audit(c,uid,'admin_created_cli','user',uid)
        finally:
            image_path.unlink(missing_ok=True)
        print('Administrator created; MFA is mandatory. Store the setup key securely for recovery.')
    elif args.command=='dev-mail':
        if s.production or s.email_mode!='file':
            raise SystemExit('Private development file mail only; disabled for SMTP/production.')
        address = input('PRIVATE: show latest development message for email: ').strip().lower()
        with db.read() as c:
            message = c.execute("SELECT subject,body FROM mail_outbox WHERE recipient=? AND status='file' ORDER BY id DESC LIMIT 1",(address,)).fetchone()
        if not message:
            raise SystemExit('No saved message for this address. Check mail-status or run mail-worker --once.')
        print('PRIVATE DEVELOPMENT FILE — NOT DELIVERED\n'+message['subject']+'\n'+message['body'])
    elif args.command=='mail-worker':
        while True:
            deliver_pending(db,s,100)
            if args.once:
                break
            time.sleep(15)
    elif args.command=='mail-status':
        with db.read() as c:
            print(dict(c.execute('SELECT status,COUNT(*) FROM mail_outbox GROUP BY status')))
    else:
        key = os.getenv('SHAMAA_BACKUP_KEY') or getpass.getpass('External backup encryption key: ')
        if args.command=='backup':
            p = create_backup(db,s,args.output,key)
            print('Encrypted backup written:',p)
        elif args.command=='backup-rotate':
            p,removed = rotate_backups(db,s,args.directory,key,args.keep_days)
            print('Encrypted backup written:',p,'; expired archives removed:',removed)
        else:
            p = restore_backup(args.input,args.to,key,s.data_dir)
            print('Restored and validated OFFLINE:',p)
            print('All sessions/tokens revoked. Review mail, environment and encryption key before starting.')


if __name__=='__main__':
    main()
