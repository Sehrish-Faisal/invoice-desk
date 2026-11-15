"""API-only local benchmark against the isolated test DB; never production data."""
import json
import platform
import secrets
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from sqlalchemy.engine import make_url
from fastapi.testclient import TestClient
from app import db
from app.main import app
from app.security import digest,COOKIE,passwords
from app.config import ROOT,config
cfg=config();url=cfg['test_database_url'];assert make_url(url).database=='invoicedesk_test'
engine=db.engine(url); tokens=[secrets.token_urlsafe(32) for _ in range(10)];owner=uuid4()
with engine.begin() as c:
    db.run(c,'TRUNCATE users,suppliers,purchase_orders,po_lines,invoices,invoice_versions,invoice_numbers,jobs,extraction_attempts,allocations,audit_events,exports,sessions,rate_limits RESTART IDENTITY CASCADE')
    db.run(c,"INSERT INTO users VALUES(:i,'benchmark',:p,'operator',true)",i=owner,p=passwords.hash(secrets.token_urlsafe(32)))
    for token in tokens:
        uid=uuid4();db.run(c,"INSERT INTO users VALUES(:i,:n,:p,'operator',true)",i=uid,n='bench-'+str(uid),p=passwords.hash(secrets.token_urlsafe(32)))
        db.run(c,"INSERT INTO sessions VALUES(:t,:u,now()+interval '1 hour')",t=digest(token),u=uid)
    db.run(c,"INSERT INTO invoices(id,sha256,media_type,page_count,uploader_id) SELECT gen_random_uuid(), 'benchmark-'||g::text,'image/png',1,:u FROM generate_series(1,10000) g",u=owner)
app.state.engine=engine
with TestClient(app,base_url='http://127.0.0.1:8030') as client:
    def batch(token):
        measurements=[]
        for _ in range(10):
            start=time.perf_counter();r=client.get('/api/invoices',headers={'cookie':COOKIE+'='+token});measurements.append((time.perf_counter()-start)*1000)
            assert r.status_code==200,r.status_code
        return measurements
    start=time.perf_counter()
    with ThreadPoolExecutor(10) as pool: times=[v for group in pool.map(batch,tokens) for v in group]
    elapsed=time.perf_counter()-start
report={'scope':'In-process HTTP/ASGI API and real PostgreSQL; excludes browser rendering, network transport and document extraction. Test DB only.','platform':platform.platform(),'python':platform.python_version(),'invoices':10000,'concurrent_users':10,'requests':len(times),'p50_ms':round(statistics.median(times),2),'p95_ms':round(sorted(times)[94],2),'elapsed_seconds':round(elapsed,2),'all_http_200':True}
(ROOT/'docs/benchmark.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
