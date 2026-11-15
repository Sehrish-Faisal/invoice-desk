"""Consistent PostgreSQL snapshot plus immutable private documents. Restore verification never overwrites a database."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime,timezone
from pathlib import Path
from sqlalchemy.engine import make_url
import psycopg
from psycopg import sql
from app import db
from app.config import STATE,config

def run_pg(command,url,*args):
    u=make_url(url); env=os.environ.copy();env['PGPASSWORD']=u.password
    subprocess.run([str(Path(config()['bin'])/command),'-h',u.host,'-p',str(u.port),'-U',u.username,'-d',u.database,*map(str,args)],env=env,check=True,capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
def backup():
    folder=STATE/'backups'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');folder.mkdir(parents=True)
    engine=db.engine(config()['migration_url'])
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as c:
        with c.begin():
            snapshot=db.run(c,'SELECT pg_export_snapshot()').scalar_one()
            records=db.rows(c,'SELECT id,sha256,page_count FROM invoices ORDER BY id')
            counts={name:db.run(c,'SELECT count(*) FROM '+name).scalar_one() for name in ['invoices','users','invoice_versions','allocations','exports','audit_events']}
            run_pg('pg_dump.exe',config()['migration_url'],'--format=custom','--no-owner','--no-acl','--snapshot='+snapshot,'--file='+str(folder/'database.dump'))
            for r in records:
                source=STATE/'documents'/str(r['id']);target=folder/'documents'/str(r['id'])
                shutil.copytree(source,target)
                if hashlib.sha256((target/'original').read_bytes()).hexdigest()!=r['sha256']: raise RuntimeError('Original document hash mismatch.')
    manifest={'created_at':datetime.now(timezone.utc).isoformat(),'counts':counts,'documents':[{**r,'id':str(r['id'])} for r in records]}
    (folder/'manifest.json').write_text(json.dumps(manifest,indent=2));engine.dispose()
    print('Private backup created:',folder);return folder
def verify(folder):
    folder=folder.resolve(); root=(STATE/'backups').resolve()
    if not folder.is_relative_to(root): raise SystemExit('Choose a backup inside the private InvoiceDesk backup directory.')
    manifest=json.loads((folder/'manifest.json').read_text()); cfg=config()
    feed=json.loads((Path(os.environ['LOCALAPPDATA'])/'FeedDesk/config.json').read_text())
    name='invoicedesk_restore_'+datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')
    with psycopg.connect(feed['admin_url'].replace('postgresql+psycopg:','postgresql:'),autocommit=True) as c:
        c.execute(sql.SQL('CREATE DATABASE {} OWNER invoicedesk_owner').format(sql.Identifier(name)))
        c.execute(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC').format(sql.Identifier(name)))
    url=make_url(cfg['migration_url']).set(database=name).render_as_string(hide_password=False)
    run_pg('pg_restore.exe',url,'--no-owner','--no-acl','--exit-on-error',folder/'database.dump')
    engine=db.engine(url)
    with engine.connect() as c:
        for table,count in manifest['counts'].items():
            if table not in ['invoices','users','invoice_versions','allocations','exports','audit_events']:raise RuntimeError('Unexpected table in manifest.')
            if db.run(c,'SELECT count(*) FROM '+table).scalar_one()!=count: raise RuntimeError('Row count mismatch.')
        records=db.rows(c,'SELECT id,sha256,page_count FROM invoices ORDER BY id')
        for r in records:
            path=folder/'documents'/str(r['id'])
            if hashlib.sha256((path/'original').read_bytes()).hexdigest()!=r['sha256']:raise RuntimeError('File hash mismatch.')
            for page in range(1,r['page_count']+1):
                if not (path/'pages'/f'{page}.png').is_file():raise RuntimeError('Missing rendered page.')
    engine.dispose()
    report={'verified_at':datetime.now(timezone.utc).isoformat(),'restored_database':name,'counts':manifest['counts'],'document_hashes_verified':len(records),'result':'passed'}
    (folder/'restore-verification.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('command',choices=['create','verify','create-and-verify']);p.add_argument('folder',nargs='?');a=p.parse_args()
    if a.command=='verify':verify(Path(a.folder))
    else:
        created=backup()
        if a.command=='create-and-verify':verify(created)
