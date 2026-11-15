import json
import os
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url
from app import db
from app.config import config
from app.main import app
from app.security import passwords

@pytest.fixture
def engine(tmp_path,monkeypatch):
    url=config()['test_database_url']
    assert make_url(url).database=='invoicedesk_test', 'Tests must use the isolated test database.'
    e=db.engine(url)
    with e.begin() as c:
        db.run(c,'TRUNCATE users,suppliers,purchase_orders,po_lines,invoices,invoice_versions,invoice_numbers,jobs,extraction_attempts,allocations,audit_events,exports,sessions,rate_limits RESTART IDENTITY CASCADE')
    monkeypatch.setattr('app.main.STATE',tmp_path)
    monkeypatch.setattr('app.worker.STATE',tmp_path)
    yield e
    e.dispose()
@pytest.fixture(scope='session')
def test_passwords():
    pw='test-'+str(uuid4())
    return pw,{name:passwords.hash(pw) for name in ['operator','approver','admin']}
@pytest.fixture
def seeded(engine,test_passwords):
    pw,hashes=test_passwords
    users={}; supplier,po,line=uuid4(),uuid4(),uuid4()
    with engine.begin() as c:
        for name,role in [('operator','operator'),('approver','approver'),('admin','admin')]:
            ident=uuid4(); users[name]={'id':ident,'username':name,'role':role}
            db.run(c,'INSERT INTO users VALUES(:i,:n,:p,:r,true)',i=ident,n=name,p=hashes[name],r=role)
        db.run(c,"INSERT INTO suppliers VALUES(:i,'Synthetic supplier')",i=supplier)
        db.run(c,"INSERT INTO purchase_orders VALUES(:i,'TEST-PO',:s,'PKR')",i=po,s=supplier)
        db.run(c,"INSERT INTO po_lines VALUES(:i,:p,'Bags',100,125.50)",i=line,p=po)
    return {'users':users,'password':pw,'supplier':supplier,'po':po,'line':line}
@pytest.fixture
def client(engine,seeded):
    app.state.engine=engine
    with TestClient(app,base_url='http://127.0.0.1:8030',raise_server_exceptions=True) as client:
        client.headers['origin']='http://127.0.0.1:8030'
        yield client
def sign_in(client,seeded,name='operator'):
    r=client.post('/api/auth/login',json={'username':name,'password':seeded['password']}); assert r.status_code==200,r.text
    client.headers['x-csrf-token']=client.get('/api/session').json()['csrf']
def review_data(seeded,number='INV-001',quantity='20',version=0):
    return {'supplier_id':str(seeded['supplier']),'po_id':str(seeded['po']),'invoice_number':number,'invoice_date':'2026-11-13','due_date':'2026-12-13','currency':'PKR','tax':'0','discount':'0','declared_total':str(__import__('decimal').Decimal(quantity)*__import__('decimal').Decimal('125.50')),'lines':[{'description':'Bags','quantity':quantity,'unit_price':'125.50','po_line_id':str(seeded['line'])}],'expected_version':version}
def invoice(engine,seeded):
    ident=uuid4()
    with engine.begin() as c: db.run(c,"INSERT INTO invoices(id,sha256,media_type,page_count,uploader_id) VALUES(:i,:h,'image/png',1,:u)",i=ident,h=str(uuid4()),u=seeded['users']['operator']['id'])
    return ident
