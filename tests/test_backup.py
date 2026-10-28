import os
from pathlib import Path
import sqlite3
from contextlib import closing
import tarfile
from io import BytesIO
import pytest
from cryptography.fernet import Fernet
from cryptography.exceptions import InvalidTag
from server.backup import HEADER, create_backup, encrypt_file, restore_backup, rotate_backups
from server.database import now
from server.security import digest


def test_encrypted_backup_and_validated_restore_with_media(factory,tmp_path):
    seller=factory.seller();product=factory.product(seller)
    buyer=factory.user();factory.cart(buyer,product);result,_,_=factory.checkout(buyer);assert result.status_code==201
    with factory.db.transaction() as c:c.execute("INSERT INTO auth_tokens VALUES(?,?,'reset',?,NULL)",(digest('generated'),buyer['id'],now()+500))
    key=Fernet.generate_key().decode();backup=tmp_path/'snapshot.shama'
    create_backup(factory.db,factory.settings,backup,key)
    content=backup.read_bytes()
    assert content.startswith(HEADER) and b'SQLite format 3' not in content
    assert os.stat(backup).st_mode & 0o077==0
    restored=restore_backup(backup,tmp_path/'restored',key,tmp_path)
    with closing(sqlite3.connect(restored/'marketplace.sqlite3')) as c:
        assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert c.execute('SELECT COUNT(*) FROM orders').fetchone()[0]==1
        assert c.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM auth_tokens WHERE used_at IS NULL').fetchone()[0]==0
        for (storage_key,) in c.execute('SELECT storage_key FROM media'):
            for size in (480,960):assert (restored/'media'/f'{storage_key}-{size}.webp').is_file()
    assert (restored/'development.key').read_bytes()==(tmp_path/'development.key').read_bytes()


def test_wrong_key_or_tampering_never_extracts(factory,tmp_path):
    key=Fernet.generate_key().decode();backup=tmp_path/'secure.shama'
    create_backup(factory.db,factory.settings,backup,key)
    with pytest.raises(InvalidTag):restore_backup(backup,tmp_path/'bad-key',Fernet.generate_key().decode(),tmp_path)
    assert not (tmp_path/'bad-key').exists()
    b=bytearray(backup.read_bytes());b[len(b)//2]^=1;backup.write_bytes(b)
    with pytest.raises(InvalidTag):restore_backup(backup,tmp_path/'tampered',key,tmp_path)
    assert not (tmp_path/'tampered').exists()


def test_restore_never_overwrites_live_files(factory,tmp_path):
    with pytest.raises(ValueError):restore_backup(tmp_path/'unused',tmp_path,Fernet.generate_key().decode(),tmp_path)


def test_restore_blocks_traversal_even_in_authenticated_archive(factory,tmp_path):
    archive=tmp_path/'unsafe.tar.gz'
    with tarfile.open(archive,'w:gz') as tar:
        entry=tarfile.TarInfo('../outside.txt');entry.size=6;tar.addfile(entry,BytesIO(b'unsafe'))
    key=Fernet.generate_key().decode();secure=tmp_path/'unsafe.shama';encrypt_file(archive,secure,key)
    with pytest.raises(ValueError):restore_backup(secure,tmp_path/'unsafe-restore',key,tmp_path)
    assert not (tmp_path/'outside.txt').exists()


def test_rotation_prunes_only_owned_archives_after_success(factory,tmp_path):
    folder=tmp_path/'archives';folder.mkdir()
    old=folder/'shamaa-20200101T000000Z-0123456789abcdef.shama';old.write_bytes(b'old')
    unrelated=folder/'other-archive.shama';unrelated.write_bytes(b'preserve')
    linked=folder/'shamaa-20200101T000000Z-aaaaaaaaaaaaaaaa.shama';linked.symlink_to(unrelated)
    os.utime(old,(1,1));os.utime(unrelated,(1,1))
    key=Fernet.generate_key().decode()
    archive,removed=rotate_backups(factory.db,factory.settings,folder,key,14)
    assert removed==1 and not old.exists() and linked.is_symlink() and unrelated.exists()
    assert archive.is_file() and archive.read_bytes().startswith(HEADER)
    assert os.stat(folder).st_mode & 0o077==0
    restored=restore_backup(archive,tmp_path/'rotation-restore',key,tmp_path)
    assert (restored/'marketplace.sqlite3').exists()


def test_rotation_failure_never_prunes_or_overwrites_live_data(factory,tmp_path):
    folder=tmp_path/'archives';folder.mkdir()
    old=folder/'shamaa-20200101T000000Z-0123456789abcdef.shama';old.write_bytes(b'preserve');os.utime(old,(1,1))
    with pytest.raises(ValueError):rotate_backups(factory.db,factory.settings,folder,'invalid',14)
    assert old.exists()
    with pytest.raises(ValueError):rotate_backups(factory.db,factory.settings,tmp_path,Fernet.generate_key().decode(),14)
    with pytest.raises(ValueError):rotate_backups(factory.db,factory.settings,folder,Fernet.generate_key().decode(),0)
