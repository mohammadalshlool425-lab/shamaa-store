"""Transactional outbox, TLS SMTP or explicit private-file development transport.
SMTP retries are at-least-once; stable Message-ID helps receivers deduplicate.
"""
from email.message import EmailMessage
from html import escape
import logging
import os
import smtplib
import ssl
from .database import now

log = logging.getLogger('shamaa.mail')


def queue(c, settings, recipient, subject, body, locale='ar'):
    direction = 'rtl' if locale == 'ar' else 'ltr'
    html = f'<html lang="{locale}" dir="{direction}"><body><h1>شمعة · SHAM’A</h1><p>{escape(body).replace(chr(10), "<br>")}</p></body></html>'
    c.execute('INSERT INTO mail_outbox(recipient,subject,body,html,available_at,created_at) VALUES(?,?,?,?,?,?)',
              (recipient, subject, body, html, now(), now()))


def notify(c, settings, user_id, kind, ar, en, email=True):
    c.execute('INSERT INTO notifications(user_id,kind,body_ar,body_en,created_at) VALUES(?,?,?,?,?)',
              (user_id,kind,ar,en,now()))
    if email:
        u = c.execute('SELECT email,locale FROM users WHERE id=?', (user_id,)).fetchone()
        queue(c, settings, u['email'], 'شمعة · SHAM’A', ar if u['locale']=='ar' else en, u['locale'])


def deliver_pending(db, settings, limit=20):
    for _ in range(limit):
        with db.transaction() as c:
            # A crashed worker's lease can be reclaimed after five minutes.
            c.execute("UPDATE mail_outbox SET status='pending' WHERE status='sending' AND available_at<?", (now()-300,))
            row = c.execute("SELECT * FROM mail_outbox WHERE status IN ('pending','failed') AND available_at<=? AND attempts<5 ORDER BY id LIMIT 1", (now(),)).fetchone()
            if not row:
                return
            m = dict(row)
            c.execute("UPDATE mail_outbox SET status='sending',attempts=attempts+1,available_at=? WHERE id=?", (now(),m['id']))
        msg = EmailMessage()
        msg['To'], msg['From'], msg['Subject'] = m['recipient'], settings.mail_from, m['subject']
        msg['Message-ID'] = f"<shamaa-{m['id']}@{settings.mail_from.split('@')[-1]}>"
        msg.set_content(m['body'])
        msg.add_alternative(m['html'], subtype='html')
        try:
            if settings.email_mode == 'file':
                folder = settings.data_dir / 'mail'
                folder.mkdir(exist_ok=True, mode=0o700)
                path = folder / f"{m['id']:08}.eml"
                fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
                with os.fdopen(fd, 'wb') as f:
                    f.write(msg.as_bytes())
                status = 'file'
            else:
                context = ssl.create_default_context()
                cls = smtplib.SMTP_SSL if settings.smtp_port == 465 else smtplib.SMTP
                kwargs = {'context': context} if settings.smtp_port == 465 else {}
                with cls(settings.smtp_host, settings.smtp_port, timeout=15, **kwargs) as smtp:
                    if settings.smtp_port != 465:
                        smtp.starttls(context=context)
                    if settings.smtp_user:
                        smtp.login(settings.smtp_user, settings.smtp_password)
                    smtp.send_message(msg)
                status = 'sent'
            with db.transaction() as c:
                c.execute('UPDATE mail_outbox SET status=?,sent_at=?,last_error=\'\' WHERE id=?', (status,now(),m['id']))
        except Exception as exc:
            # Never record provider responses, credentials or recipient addresses in logs.
            log.warning('Mail delivery failed for outbox id=%s (%s)', m['id'], type(exc).__name__)
            with db.transaction() as c:
                c.execute("UPDATE mail_outbox SET status='failed',last_error=?,available_at=? WHERE id=?",
                          (type(exc).__name__,now()+60*2**m['attempts'],m['id']))
