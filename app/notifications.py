"""Transactional, recipient-scoped inbox and bounded SMTP outbox.

SMTP is at-least-once: an ambiguous timeout/crash after acceptance can cause
a duplicate. Stable Message-ID helps receivers correlate those deliveries.
"""
import json
import re
import smtplib
import ssl
from email.message import EmailMessage
from uuid import uuid4
from . import db
from .config import STATE

TITLES = {
    'submitted': 'Invoice awaiting your approval',
    'approved': 'Invoice approved',
    'rejected': 'Invoice returned for correction',
    'extraction_done': 'AI draft ready for review',
    'extraction_failed': 'AI extraction failed; manual review is available',
}

def record_event(c,event,action,invoice_id):
    if action not in TITLES or invoice_id is None: return
    inv=db.one(c,'SELECT uploader_id,reviewer_id FROM invoices WHERE id=:i',i=invoice_id)
    if not inv: return
    if action=='submitted':
        recipients=db.rows(c,"SELECT id FROM users WHERE active AND role IN ('admin','approver') AND id<>:u AND (CAST(:r AS uuid) IS NULL OR id<>:r)",u=inv['uploader_id'],r=inv['reviewer_id'])
    else:
        recipients=db.rows(c,'SELECT id FROM users WHERE active AND (id=:u OR id=:r)',u=inv['uploader_id'],r=inv['reviewer_id'])
    for user in recipients:
        ident=uuid4()
        inserted=db.run(c,'INSERT INTO notifications(id,event_id,user_id,invoice_id,title) VALUES(:i,:e,:u,:v,:t) ON CONFLICT(event_id,user_id) DO NOTHING RETURNING id',i=ident,e=event,u=user['id'],v=invoice_id,t=TITLES[action]).scalar_one_or_none()
        if inserted:
            db.run(c,"INSERT INTO notification_emails(notification_id) SELECT :i FROM notification_contacts WHERE user_id=:u AND email_enabled",i=ident,u=user['id'])

def valid_email(value):
    return isinstance(value,str) and len(value)<=254 and re.fullmatch(r'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}',value) is not None

def settings():
    path=STATE/'smtp.dpapi'
    if not path.exists(): return None
    from .credential_store import protect
    cfg=json.loads(protect(path.read_bytes(),decrypt=True))
    validate_settings(cfg)
    return cfg

def validate_settings(cfg):
    if not isinstance(cfg,dict) or not re.fullmatch(r'[A-Za-z0-9.-]{1,253}',cfg.get('host','')):
        raise ValueError('Enter a valid SMTP hostname.')
    if cfg.get('mode') not in ('starttls','ssl') or not isinstance(cfg.get('port'),int) or not 1<=cfg['port']<=65535:
        raise ValueError('SMTP requires TLS and a valid port.')
    if not valid_email(cfg.get('sender')) or not cfg.get('username') or not cfg.get('password'):
        raise ValueError('Enter a sender address and SMTP credentials.')

def claim_email(engine):
    with engine.begin() as c:
        db.run(c,"UPDATE notification_emails SET status='failed',error='Delivery attempts exhausted.',lease_token=NULL,lease_until=NULL WHERE attempts>=3 AND (status='queued' OR (status='sending' AND lease_until<now()))")
        row=db.one(c,"SELECT notification_id FROM notification_emails WHERE attempts<3 AND ((status='queued' AND available_at<=now()) OR (status='sending' AND lease_until<now())) ORDER BY available_at FOR UPDATE SKIP LOCKED LIMIT 1")
        if not row: return None
        token=uuid4()
        return dict(db.one(c,"UPDATE notification_emails SET status='sending',attempts=attempts+1,lease_token=:t,lease_until=now()+interval '5 minutes' WHERE notification_id=:i RETURNING *",t=token,i=row['notification_id']))

def deliver(cfg,recipient,job):
    message=EmailMessage()
    message['From']=cfg['sender']; message['To']=recipient['email']
    message['Subject']='InvoiceDesk: '+recipient['title']
    message['Message-ID']=f'<{job["notification_id"]}@{cfg["sender"].split("@")[1]}>'
    # No invoice contents, attachments, approval links, or private local URLs.
    message.set_content(recipient['title']+'.\n\nOpen your InvoiceDesk workspace and check Notifications. Sign in to view the invoice.\n\nYou can disable email notifications in your notification inbox.')
    context=ssl.create_default_context()
    cls=smtplib.SMTP_SSL if cfg['mode']=='ssl' else smtplib.SMTP
    kwargs={'context':context} if cfg['mode']=='ssl' else {}
    with cls(cfg['host'],cfg['port'],timeout=15,**kwargs) as smtp:
        if cfg['mode']=='starttls': smtp.ehlo(); smtp.starttls(context=context); smtp.ehlo()
        smtp.login(cfg['username'],cfg['password'])
        smtp.send_message(message)

def send_once(engine):
    try: cfg=settings()
    except Exception: return False  # Private config may be unavailable; inbox remains operational.
    if not cfg: return False
    job=claim_email(engine)
    if not job: return False
    with engine.connect() as c:
        recipient=db.one(c,'SELECT n.title,p.email,p.email_enabled,u.active FROM notifications n JOIN users u ON u.id=n.user_id LEFT JOIN notification_contacts p ON p.user_id=n.user_id WHERE n.id=:i',i=job['notification_id'])
    status='cancelled'; error=None
    if recipient and recipient['active'] and recipient['email_enabled'] and valid_email(recipient['email']):
        try: deliver(cfg,recipient,job); status='sent'
        except Exception:
            status='failed' if job['attempts']>=3 else 'queued'
            error='Email delivery failed. Check the SMTP configuration or recipient address.'
    with engine.begin() as c:
        db.run(c,"UPDATE notification_emails SET status=:s,error=:e,available_at=now()+(:delay*interval '1 second'),sent_at=CASE WHEN :s='sent' THEN now() ELSE NULL END,lease_token=NULL,lease_until=NULL WHERE notification_id=:i AND status='sending' AND lease_token=:t",s=status,e=error,delay=60*job['attempts'],i=job['notification_id'],t=job['lease_token'])
    return True
