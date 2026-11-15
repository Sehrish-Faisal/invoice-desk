"""Explicit local administration; secrets are never printed by setup."""
import argparse
import json
import os
import secrets
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4
from urllib.parse import quote
import psycopg
from psycopg import sql
from app import db
from app.config import ROOT,STATE,config
from app.security import passwords

def private_dir():
    STATE.mkdir(parents=True,exist_ok=True)
    if os.name=='nt':
        who=subprocess.check_output(['whoami'],text=True).strip()
        subprocess.run(['icacls',str(STATE),'/inheritance:r','/grant:r',f'{who}:(OI)(CI)F','SYSTEM:(OI)(CI)F'],check=True,stdout=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
def migrate(url):
    with psycopg.connect(url.replace('postgresql+psycopg:','postgresql:')) as c:
        if c.execute("SELECT to_regclass('schema_versions')").fetchone()[0] is None:
            c.execute((ROOT/'migrations/001_initial.sql').read_text())
        installed={r[0] for r in c.execute('SELECT version FROM schema_versions').fetchall()}
        for path in sorted((ROOT/'migrations').glob('*.sql')):
            if int(path.name.split('_')[0]) not in installed:
                c.execute(path.read_text())
def add_user(username,role):
    if not __import__('re').fullmatch(r'[a-z0-9_.-]{1,80}',username): raise SystemExit('Use a lowercase username with letters, numbers, dots or hyphens.')
    engine=db.engine(config()['migration_url']); pw=secrets.token_urlsafe(20)
    with engine.begin() as c:
        db.run(c,'INSERT INTO users VALUES(:i,:n,:p,:r,true)',i=uuid4(),n=username,p=passwords.hash(pw),r=role)
        db.audit(c,None,'staff_provisioned',username=username,role=role)
    with (STATE/'credentials.txt').open('a',encoding='utf-8') as f: f.write(f'{username}: {pw}\n')
    engine.dispose()
def disable_user(username):
    engine=db.engine(config()['migration_url'])
    with engine.begin() as c:
        u=db.one(c,'UPDATE users SET active=false WHERE username=:n RETURNING id',n=username)
        if not u: raise SystemExit('User not found.')
        db.run(c,'DELETE FROM sessions WHERE user_id=:i',i=u['id'])
        db.audit(c,None,'staff_disabled',u['id'])
    engine.dispose();print('Account disabled and sessions revoked.')
def reset_password(username):
    engine=db.engine(config()['migration_url']);pw=secrets.token_urlsafe(20)
    with engine.begin() as c:
        u=db.one(c,'UPDATE users SET password_hash=:p WHERE username=:n RETURNING id',p=passwords.hash(pw),n=username)
        if not u:raise SystemExit('User not found.')
        db.run(c,'DELETE FROM sessions WHERE user_id=:i',i=u['id'])
        db.audit(c,None,'staff_password_reset',u['id'])
    target=STATE/'credentials.txt';content=target.read_text(encoding='utf-8') if target.exists() else ''
    lines=[line for line in content.splitlines() if not line.startswith(username+':')]
    temporary=target.with_suffix('.tmp');temporary.write_text('\n'.join(lines+[f'{username}: {pw}'])+'\n',encoding='utf-8');os.replace(temporary,target)
    engine.dispose();print('Random replacement password saved privately; all account sessions revoked.')
def setup():
    private_dir()
    if (STATE/'config.json').exists(): print('Existing InvoiceDesk configuration retained.'); return
    # Reuse the installed cluster, but create separate roles and databases.
    feed=Path(os.environ['LOCALAPPDATA'])/'FeedDesk'; fc=json.loads((feed/'config.json').read_text())
    subprocess.run([str(Path(fc['bin'])/'pg_ctl.exe'),'-D',fc['data'],'-l',str(feed/'postgres.log'),'-w','start'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
    owner=secrets.token_urlsafe(32); apppw=secrets.token_urlsafe(32)
    with psycopg.connect(fc['admin_url'].replace('postgresql+psycopg:','postgresql:'),autocommit=True) as c:
        for name,pw in [('invoicedesk_owner',owner),('invoicedesk_app',apppw)]:
            c.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE').format(sql.Identifier(name),sql.Literal(pw)))
        for name in ['invoicedesk','invoicedesk_test']:
            c.execute(sql.SQL('CREATE DATABASE {} OWNER invoicedesk_owner').format(sql.Identifier(name)))
            c.execute(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC').format(sql.Identifier(name)))
        c.execute('GRANT CONNECT ON DATABASE invoicedesk TO invoicedesk_app')
    def url(user,pw,name): return f'postgresql+psycopg://{user}:{quote(pw)}@127.0.0.1:{fc["port"]}/{name}'
    cfg={'database_url':url('invoicedesk_app',apppw,'invoicedesk'),'migration_url':url('invoicedesk_owner',owner,'invoicedesk'),
         'test_database_url':url('invoicedesk_owner',owner,'invoicedesk_test'),'bin':fc['bin'],'cluster_data':fc['data']}
    (STATE/'config.json').write_text(json.dumps(cfg),encoding='utf-8')
    for target in ['migration_url','test_database_url']: migrate(cfg[target])
    engine=db.engine(cfg['migration_url'])
    with engine.begin() as c:
        db.run(c,'REVOKE CREATE ON SCHEMA public FROM PUBLIC'); db.run(c,'GRANT USAGE ON SCHEMA public TO invoicedesk_app')
        db.run(c,'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO invoicedesk_app')
        db.run(c,'GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO invoicedesk_app')
        db.run(c,'REVOKE ALL ON schema_versions FROM invoicedesk_app')
        db.run(c,'REVOKE INSERT,UPDATE,DELETE ON users FROM invoicedesk_app')
        db.run(c,'REVOKE UPDATE,DELETE ON audit_events,invoice_versions,allocations,exports,extraction_attempts FROM invoicedesk_app')
        sid,poid,lineid=uuid4(),uuid4(),uuid4()
        db.run(c,'INSERT INTO suppliers VALUES(:i,:n)',i=sid,n='Demo — Northstar Supplies')
        db.run(c,"INSERT INTO purchase_orders VALUES(:i,'PO-2026-001',:s,'PKR')",i=poid,s=sid)
        db.run(c,"INSERT INTO po_lines VALUES(:i,:p,'Packaging bags',100,125.50)",i=lineid,p=poid)
        db.audit(c,None,'synthetic_reference_data_created')
        db.run(c,'REVOKE INSERT,UPDATE,DELETE ON notification_contacts FROM invoicedesk_app'); db.run(c,'GRANT UPDATE(email_enabled) ON notification_contacts TO invoicedesk_app')
    engine.dispose()
    (STATE/'credentials.txt').write_text('InvoiceDesk local staff credentials — keep private\nhttp://127.0.0.1:8030\n\n',encoding='utf-8')
    for username,role in [('admin','admin'),('operator','operator'),('approver','approver')]: add_user(username,role)
    # Copy only the currently saved encrypted key, never an old environment file.
    if (feed/'ai-credentials.dpapi').exists(): shutil.copyfile(feed/'ai-credentials.dpapi',STATE/'ai-credentials.dpapi')
    print('InvoiceDesk initialized. Private credentials:',STATE/'credentials.txt')
if __name__=='__main__':
    parser=argparse.ArgumentParser(); sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('setup'); sub.add_parser('migrate'); staff=sub.add_parser('add-user'); staff.add_argument('username'); staff.add_argument('role',choices=['admin','operator','approver'])
    disabled=sub.add_parser('disable-user');disabled.add_argument('username')
    reset=sub.add_parser('reset-password');reset.add_argument('username')
    args=parser.parse_args()
    if args.command=='setup': setup()
    elif args.command=='migrate':
        for target in ['migration_url','test_database_url']: migrate(config()[target])
        print('Database migrations applied.')
    elif args.command=='disable-user': disable_user(args.username)
    elif args.command=='reset-password': reset_password(args.username)
    else: add_user(args.username,args.role); print('Generated credentials saved to the private credentials file.')
