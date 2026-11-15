"""Isolated InvoiceDesk API process used only by FeedDesk's integration test."""
import json
import os
import secrets
from pathlib import Path
from uuid import uuid4
import uvicorn
from sqlalchemy.engine import make_url
from app import db
from app.config import config
from app.security import passwords,digest
from app.main import app

if __name__=='__main__':
    target=Path(os.environ['BRIDGE_TEST_STATE'])
    url=config()['test_database_url']
    assert make_url(url).database=='invoicedesk_test'
    engine=db.engine(url);pw=secrets.token_urlsafe(20);key='idk_'+secrets.token_urlsafe(32);connection=uuid4()
    with engine.begin() as c:
        db.run(c,'TRUNCATE users,suppliers,purchase_orders,po_lines,invoices,invoice_versions,invoice_numbers,jobs,extraction_attempts,allocations,audit_events,exports,sessions,rate_limits RESTART IDENTITY CASCADE')
        admin=None
        for role in ['admin','operator','approver']:
            ident=uuid4();db.run(c,'INSERT INTO users VALUES(:i,:u,:p,:r,true)',i=ident,u=role,p=passwords.hash(pw),r=role)
            if role=='admin':admin=ident
        db.run(c,"INSERT INTO integrations(id,name,created_by,scopes,token_hash,token_expires_at) VALUES(:i,'Bridge test',:u,'[\"reference:write\",\"invoices:read\"]',:h,now()+interval '1 day')",i=connection,u=admin,h=digest(key))
    import app.main as main
    main.STATE=target
    app.state.engine=engine
    (target/'fixture.json').write_text(json.dumps({'connection_id':str(connection),'api_key':key,'password':pw}))
    uvicorn.run(app,host='127.0.0.1',port=int(os.environ['BRIDGE_TEST_PORT']),access_log=False,log_level='error')
