"""Initialize only an ephemeral CI PostgreSQL service."""
import json
import os
import secrets
from pathlib import Path
import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url
from app.config import STATE
from manage import migrate
from app import db
assert os.environ.get('CI')=='true','CI setup must run in an ephemeral CI job.'
admin=make_url(os.environ['INVOICEDESK_CI_ADMIN_URL'])
assert admin.host in ('localhost','127.0.0.1') and admin.database=='postgres'
owner=secrets.token_urlsafe(32);runtime=secrets.token_urlsafe(32)
with psycopg.connect(admin.render_as_string(hide_password=False),autocommit=True) as c:
    for name,pw in [('invoicedesk_owner',owner),('invoicedesk_app',runtime)]:c.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE').format(sql.Identifier(name),sql.Literal(pw)))
    for name in ['invoicedesk','invoicedesk_test']:c.execute(sql.SQL('CREATE DATABASE {} OWNER invoicedesk_owner').format(sql.Identifier(name)))
ownerurl=admin.set(drivername='postgresql+psycopg',username='invoicedesk_owner',password=owner,database='invoicedesk')
cfg={'migration_url':ownerurl.render_as_string(hide_password=False),'database_url':ownerurl.set(username='invoicedesk_app',password=runtime).render_as_string(hide_password=False),'test_database_url':ownerurl.set(database='invoicedesk_test').render_as_string(hide_password=False)}
STATE.mkdir(parents=True,exist_ok=True);(STATE/'config.json').write_text(json.dumps(cfg));os.chmod(STATE/'config.json',0o600)
for target in ['migration_url','test_database_url']:migrate(cfg[target])
e=db.engine(cfg['migration_url'])
with e.begin() as c:
    for query in ['REVOKE CREATE ON SCHEMA public FROM PUBLIC','GRANT USAGE ON SCHEMA public TO invoicedesk_app','GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO invoicedesk_app','GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO invoicedesk_app','REVOKE ALL ON schema_versions FROM invoicedesk_app','REVOKE INSERT,UPDATE,DELETE ON users FROM invoicedesk_app','REVOKE UPDATE,DELETE ON audit_events,invoice_versions,allocations,exports,extraction_attempts FROM invoicedesk_app']:db.run(c,query)
    db.run(c,'REVOKE INSERT,UPDATE,DELETE ON notification_contacts FROM invoicedesk_app'); db.run(c,'GRANT UPDATE(email_enabled) ON notification_contacts TO invoicedesk_app')
e.dispose();print('Ephemeral CI databases initialized.')
