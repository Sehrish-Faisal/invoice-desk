"""One-time local administrator pairing; never prints or accepts secrets as arguments."""
import json
import os
import secrets
from pathlib import Path
from uuid import uuid4
from app import db
from app.config import config
from app.security import digest
from app.credential_store import protect

def main():
    state=Path(os.environ['LOCALAPPDATA'])/'FeedDesk';target=state/'invoicedesk.dpapi'
    if not (state/'config.json').exists():raise SystemExit('Configure FeedDesk first.')
    engine=db.engine(config()['migration_url'])
    try:
        if target.exists():
            value=json.loads(protect(target.read_bytes(),decrypt=True))
            with engine.connect() as c:
                valid=db.one(c,"SELECT id FROM integrations WHERE id=:i AND token_hash=:h AND active AND token_expires_at>now()",i=value['connection_id'],h=digest(value['api_key']))
            if not valid:raise SystemExit('Existing pairing is disabled or expired. Review it before replacing credentials.')
            print('Existing FeedDesk pairing retained.');return
        key='idk_'+secrets.token_urlsafe(32);ident=uuid4()
        # Write encrypted temporary file first; no plaintext credential on disk.
        temporary=target.with_suffix('.tmp')
        temporary.write_bytes(protect(json.dumps({'connection_id':str(ident),'api_key':key}).encode()))
        try:
            with engine.begin() as c:
                if db.one(c,"SELECT id FROM integrations WHERE name='FeedDesk'"):raise SystemExit('A FeedDesk connection already exists. Inspect it before pairing.')
                actor=db.one(c,"SELECT id FROM users WHERE username='admin' AND active AND role='admin'")
                if not actor:raise SystemExit('An active InvoiceDesk admin account is required.')
                db.run(c,"INSERT INTO integrations(id,name,created_by,scopes,token_hash,token_expires_at) VALUES(:i,'FeedDesk',:u,'[\"reference:write\",\"invoices:read\"]',:h,now()+interval '90 days')",i=ident,u=actor['id'],h=digest(key))
                db.audit(c,actor['id'],'feeddesk_paired',ident)
            temporary.replace(target)
        finally:temporary.unlink(missing_ok=True)
        print('FeedDesk paired. Scoped key stored encrypted in its private state directory; no webhook required.')
    finally:engine.dispose()

if __name__=='__main__':main()
