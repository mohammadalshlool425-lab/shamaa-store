"""Authenticated streaming encryption; consistent SQLite backup, immutable media.
Keys are external. Restore only into a NEW directory, then validate before rollout.
"""
import base64
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import tarfile
import tempfile
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from .database import now
from .config import ROOT

HEADER = b'SHAMA-BK1'


def key_bytes(key):
    try:
        value = base64.urlsafe_b64decode(key)
    except Exception as exc:
        raise ValueError('Invalid backup key') from exc
    if len(value)!=32:
        raise ValueError('Backup key must be a URL-safe base64 encoded 32-byte key')
    return value


def encrypt_file(source,destination,key):
    nonce = secrets.token_bytes(12)
    cipher = Cipher(algorithms.AES(key_bytes(key)),modes.GCM(nonce)).encryptor()
    cipher.authenticate_additional_data(HEADER)
    fd = os.open(destination,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    try:
        with open(source,'rb') as inp,os.fdopen(fd,'wb') as out:
            out.write(HEADER+nonce)
            while chunk:=inp.read(64*1024):
                out.write(cipher.update(chunk))
            out.write(cipher.finalize())
            out.write(cipher.tag)
    except BaseException:
        Path(destination).unlink(missing_ok=True)
        raise


def decrypt_file(source,destination,key):
    size = Path(source).stat().st_size
    with open(source,'rb') as inp:
        if inp.read(len(HEADER))!=HEADER or size<len(HEADER)+28:
            raise ValueError('Not a SHAM’A encrypted backup')
        nonce = inp.read(12)
        inp.seek(-16,2)
        tag = inp.read(16)
        inp.seek(len(HEADER)+12)
        decryptor = Cipher(algorithms.AES(key_bytes(key)),modes.GCM(nonce,tag)).decryptor()
        decryptor.authenticate_additional_data(HEADER)
        remaining = size-len(HEADER)-12-16
        try:
            fd = os.open(destination,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(fd,'wb') as out:
                while remaining:
                    chunk = inp.read(min(64*1024,remaining))
                    if not chunk:
                        raise ValueError('Truncated backup')
                    remaining -= len(chunk)
                    out.write(decryptor.update(chunk))
                out.write(decryptor.finalize()) # authenticate BEFORE any extraction
        except BaseException:
            Path(destination).unlink(missing_ok=True)
            raise


def create_backup(db,settings,destination,key):
    key_bytes(key)
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with tempfile.TemporaryDirectory(dir=settings.data_dir) as tmp:
        tmp = Path(tmp)
        snapshot = tmp/'marketplace.sqlite3'
        with db.read() as source,closing(sqlite3.connect(snapshot)) as target:
            source.backup(target)
        snapshot.chmod(0o600)
        with closing(sqlite3.connect(snapshot)) as c:
            media = list(c.execute('SELECT storage_key FROM media'))
        info = tmp/'backup-info.json'
        info.write_text(json.dumps({'version':1,'created_at':now(),'media_records':len(media)}))
        archive = tmp/'backup.tar.gz'
        with tarfile.open(archive,'w:gz') as tar:
            tar.add(snapshot,arcname='marketplace.sqlite3')
            tar.add(info,arcname='backup-info.json')
            for (storage_key,) in media:
                if not re.fullmatch('[0-9a-f]{32}',storage_key):
                    raise ValueError('Invalid media storage key')
                for size in (480,960):
                    p = settings.data_dir/'media'/f'{storage_key}-{size}.webp'
                    if not p.is_file() or p.is_symlink():
                        raise ValueError('Missing or unsafe media file; backup aborted')
                    tar.add(p,arcname='media/'+p.name)
            dev_key = settings.data_dir/'development.key'
            if not settings.production and dev_key.is_file():
                tar.add(dev_key,arcname='development.key')
        encrypt_file(archive,destination,key)
    return destination


def restore_backup(source,destination,key,scratch):
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError('Restore destination must NOT exist. Never overwrite the live database.')
    with tempfile.TemporaryDirectory(dir=scratch) as tmp:
        tmp = Path(tmp)
        archive = tmp/'decrypted.tar.gz'
        decrypt_file(source,archive,key)
        staging = tmp/'staging'
        staging.mkdir(mode=0o700)
        total = 0
        with tarfile.open(archive,'r:gz') as tar:
            for member in tar:
                if not member.isfile() or not re.fullmatch(r'(marketplace\.sqlite3|backup-info\.json|development\.key|media/[0-9a-f]{32}-(480|960)\.webp)',member.name):
                    raise ValueError('Unsafe archive entry')
                total += member.size
                if total>10*1024**3:
                    raise ValueError('Restore exceeds the 10 GB safety limit')
                target = staging/member.name
                target.parent.mkdir(exist_ok=True,mode=0o700)
                if target.exists():
                    raise ValueError('Duplicate archive entry')
                with tar.extractfile(member) as inp,open(target,'xb') as out:
                    shutil.copyfileobj(inp,out)
                target.chmod(0o600)
        database = staging/'marketplace.sqlite3'
        if not database.is_file():
            raise ValueError('Missing database snapshot')
        with closing(sqlite3.connect(database)) as c:
            if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or c.execute('PRAGMA foreign_key_check').fetchone():
                raise ValueError('Database integrity validation failed')
            if c.execute('PRAGMA user_version').fetchone()[0]!=1:
                raise ValueError('Unsupported schema version')
            for (storage_key,) in c.execute('SELECT storage_key FROM media'):
                for size in (480,960):
                    if not (staging/'media'/f'{storage_key}-{size}.webp').is_file():
                        raise ValueError('Missing referenced media')
            c.execute('DELETE FROM sessions')
            c.execute('UPDATE auth_tokens SET used_at=? WHERE used_at IS NULL',(now(),))
            c.execute("UPDATE mail_outbox SET status='failed',attempts=5,last_error='Restored backup - manual review' WHERE status IN ('pending','failed','sending')")
            c.execute("INSERT INTO audit_log(action,entity,entity_id,detail,created_at) VALUES('backup_restored','system','1','Sessions and outstanding tokens revoked',?)",(now(),))
            c.commit()
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copytree(staging,destination)
    return destination


def rotate_backups(db,settings,directory,key,keep_days=14):
    """A scheduler-safe job; retention runs ONLY after a successful new backup.

    Only our precisely named, regular archives are pruned. Offsite replication and
    scheduler enablement are operator responsibilities, not assumed here.
    """
    if not 1<=keep_days<=3650:
        raise ValueError('Backup retention must be between 1 and 3650 days')
    directory=Path(directory).resolve()
    if directory in (ROOT,settings.data_dir.resolve(),Path(directory.anchor)):
        raise ValueError('Use a dedicated backup directory, separate from live data')
    key_bytes(key) # fail before any filesystem/retention changes
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    directory.chmod(0o700)
    stamp=datetime.fromtimestamp(now(),timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    path=directory/f'shamaa-{stamp}-{secrets.token_hex(8)}.shama'
    create_backup(db,settings,path,key)
    removed=0
    for candidate in directory.iterdir():
        if (re.fullmatch(r'shamaa-\d{8}T\d{6}Z-[0-9a-f]{16}\.shama',candidate.name)
            and candidate!=path and not candidate.is_symlink() and candidate.is_file()
            and candidate.stat().st_mtime<now()-keep_days*86400):
            candidate.unlink()
            removed+=1
    return path,removed
