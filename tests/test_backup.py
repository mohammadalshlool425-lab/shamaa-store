import json
import hashlib
import os
from pathlib import Path
import sqlite3
from contextlib import closing
from io import BytesIO
import tarfile
from io import BytesIO
import pytest
from PIL import Image
from cryptography.fernet import Fernet
from cryptography.exceptions import InvalidTag
from server.backup import HEADER, create_backup, encrypt_file, restore_backup, rotate_backups
from server.database import now, Database, SCHEMA_PATH
from server.security import digest


def test_encrypted_backup_and_validated_restore_with_media(factory,tmp_path):
    seller=factory.seller();product=factory.product(seller)
    buyer=factory.user();factory.cart(buyer,product);result,_,_=factory.checkout(buyer);assert result.status_code==201
    shipment=result.json()['order']['shipments'][0]
    previous='new'
    for status in ('confirmed','preparing','shipped'):
        changed=seller['client'].patch(
            f"/api/seller/orders/{shipment['id']}/status",
            json={"status":status,"expected_status":previous},
        )
        assert changed.status_code==200,changed.text
        previous=status
    opened=buyer['client'].post('/api/cases',json={
        "seller_order_id":shipment['id'],"reason":"not_received",
        "description":"لم تصل الشحنة وأحتاج إلى إرفاق صورة دليل في النسخة الاحتياطية.",
    })
    assert opened.status_code==201,opened.text
    image=BytesIO();Image.new('RGB',(80,60),(25,75,125)).save(image,format='PNG')
    uploaded=buyer['client'].post(
        f"/api/cases/{opened.json()['case']['id']}/attachments",
        files={"file":("evidence.png",image.getvalue(),"image/png")},
    )
    assert uploaded.status_code==201,uploaded.text
    delivered=seller['client'].patch(
        f"/api/seller/orders/{shipment['id']}/status",
        json={"status":"delivered","expected_status":"shipped"},
    )
    assert delivered.status_code==200,delivered.text
    with factory.db.read() as c:
        order_item_id=c.execute(
            "SELECT id FROM order_items WHERE seller_order_id=?",(shipment['id'],)
        ).fetchone()["id"]
    return_open=buyer['client'].post('/api/returns',json={
        "order_item_id":order_item_id,"quantity":1,"reason":"damaged",
        "description":"وصل المنتج متضررًا وأريد تسجيل طلب إرجاع مع النسخة الاحتياطية.",
    })
    assert return_open.status_code==201,return_open.text
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
        assert c.execute('SELECT COUNT(*) FROM return_requests').fetchone()[0]==1
        assert c.execute('SELECT COUNT(*) FROM return_request_events').fetchone()[0]==1
        assert c.execute("SELECT status FROM return_requests").fetchone()[0]=='requested'
        assert c.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM auth_tokens WHERE used_at IS NULL').fetchone()[0]==0
        for (storage_key,) in c.execute('SELECT storage_key FROM media'):
            for size in (480,960):assert (restored/'media'/f'{storage_key}-{size}.webp').is_file()
        attachments=c.execute('SELECT storage_key,digest,bytes FROM order_case_attachments').fetchall()
        assert len(attachments)==1
        for storage_key,attachment_digest,bytes_count in attachments:
            path=restored/'case-attachments'/f'{storage_key}.webp'
            assert path.is_file() and path.stat().st_size==bytes_count
            assert hashlib.sha256(path.read_bytes()).hexdigest()==attachment_digest
    assert json.loads((restored/'backup-info.json').read_text())['case_attachment_records']==1
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



def test_restore_accepts_v1_backup_and_application_initializer_upgrades_it(factory, tmp_path):
    legacy_dir = tmp_path / "legacy-source"
    legacy_dir.mkdir()
    legacy_db = legacy_dir / "marketplace.sqlite3"
    with closing(sqlite3.connect(legacy_db)) as connection:
        connection.executescript(
            SCHEMA_PATH.read_text(encoding="utf-8") + "\nPRAGMA user_version=1;"
        )
        connection.commit()

    info = legacy_dir / "backup-info.json"
    info.write_text('{"version":1,"created_at":1,"media_records":0}', encoding="utf-8")
    archive = tmp_path / "legacy-v1.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(legacy_db, arcname="marketplace.sqlite3")
        tar.add(info, arcname="backup-info.json")

    key = Fernet.generate_key().decode()
    encrypted = tmp_path / "legacy-v1.shama"
    encrypt_file(archive, encrypted, key)
    restored = restore_backup(encrypted, tmp_path / "restored-v1", key, tmp_path)
    restored_db = restored / "marketplace.sqlite3"

    with closing(sqlite3.connect(restored_db)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    # The ordinary application initializer performs the transactional forward migration.
    Database(restored_db).initialize()
    with closing(sqlite3.connect(restored_db)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 9
