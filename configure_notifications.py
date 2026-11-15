"""Local administrator setup. No credentials in shell arguments or console output."""
import argparse
import getpass
import json
from app import db
from app.config import STATE,config
from app.notifications import validate_settings,valid_email
from app.credential_store import protect
from manage import private_dir

def configure_smtp():
    cfg={'host':input('SMTP hostname: ').strip(),
         'port':int(input('SMTP port (587 for STARTTLS, 465 for TLS): ').strip()),
         'mode':input('TLS mode (starttls / ssl): ').strip().lower(),
         'sender':input('Sender email address: ').strip(),
         'username':input('SMTP username: ').strip(),
         'password':getpass.getpass('SMTP password / app password (hidden): ')}
    validate_settings(cfg);private_dir()
    temporary=STATE/'smtp.tmp'
    temporary.write_bytes(protect(json.dumps(cfg).encode()))
    temporary.replace(STATE/'smtp.dpapi')
    print('Encrypted SMTP configuration saved. No test email was sent. The worker will deliver queued notifications.')

def configure_contact():
    username=input('Existing InvoiceDesk username: ').strip()
    address=input('Staff email address: ').strip()
    if not valid_email(address): raise ValueError('Enter a valid email address.')
    engine=db.engine(config()['migration_url'])
    try:
        with engine.begin() as c:
            user=db.one(c,'SELECT id FROM users WHERE username=:n AND active',n=username)
            if not user: raise ValueError('Active staff account not found.')
            # Avoid delivering stale notifications to a newly assigned address.
            db.run(c,"UPDATE notification_emails SET status='cancelled' WHERE status='queued' AND notification_id IN (SELECT id FROM notifications WHERE user_id=:u)",u=user['id'])
            db.run(c,'INSERT INTO notification_contacts(user_id,email) VALUES(:u,:e) ON CONFLICT(user_id) DO UPDATE SET email=excluded.email',u=user['id'],e=address)
            db.audit(c,None,'notification_contact_configured',user['id'])
    finally: engine.dispose()
    print('Staff email address registered. Existing opt-out preferences are preserved.')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['smtp','contact']);args=parser.parse_args()
    try: configure_smtp() if args.command=='smtp' else configure_contact()
    except (ValueError,OSError,RuntimeError) as exc: print('Configuration not saved. Check your entries and private configuration access.');raise SystemExit(1) from None
