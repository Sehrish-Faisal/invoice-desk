import hashlib
import hmac
import importlib.util
import json
import time
from uuid import uuid4
from pathlib import Path
import pytest
from fastapi import HTTPException
from app import db,integrations as it,security,webhooks,services,schemas
from conftest import sign_in,invoice,review_data

def make(engine,seeded,scopes=None):
    ident=uuid4();key='idk_'+str(uuid4())
    with engine.begin() as c:
        db.run(c,"INSERT INTO integrations(id,name,created_by,scopes,token_hash,token_expires_at) VALUES(:i,:n,:u,CAST(:s AS jsonb),:h,now()+interval '1 day')",i=ident,n=str(ident),u=seeded['users']['admin']['id'],s=json.dumps(scopes if scopes is not None else ['reference:write','invoices:read']),h=security.digest(key))
    return ident,key

def machine(client,key):
    client.cookies.clear();client.headers.pop('origin',None);client.headers.pop('x-csrf-token',None)
    client.headers['authorization']='Bearer '+key

SUPPLIER={'external_id':'S-1','name':'Imported packaging supplier'}
ORDER={'external_id':'PO-1','reference':'IMPORTED-PO-1','supplier_external_id':'S-1','currency':'PKR','lines':[{'external_id':'L-1','description':'Packaging bags','quantity':'100','unit_price':'125.50'}]}

def imported(engine,seeded):
    ident,key=make(engine,seeded)
    supplier=it.import_records(engine,ident,'suppliers',[SUPPLIER],False)['items'][0]['id']
    po=it.import_records(engine,ident,'purchase_orders',[ORDER],False)['items'][0]['id']
    with engine.connect() as c: line=str(db.run(c,'SELECT id FROM po_lines WHERE po_id=:p',p=po).scalar_one())
    return ident,key,{'supplier':supplier,'po':po,'line':line}

def test_admin_setup_csrf_and_one_time_key(client,seeded,engine):
    assert client.get('/api/integrations').status_code==401
    sign_in(client,seeded)
    assert client.get('/api/integrations').status_code==403
    sign_in(client,seeded,'admin')
    payload={'name':'ERP','scopes':['reference:write']}
    token=client.headers.pop('x-csrf-token')
    assert client.post('/api/integrations',json=payload).status_code==403
    client.headers['x-csrf-token']=token
    result=client.post('/api/integrations',json=payload);assert result.status_code==200
    value=result.json();assert value['api_key'].startswith('idk_')
    listed=client.get('/api/integrations');assert value['api_key'] not in listed.text
    with engine.connect() as c:
        saved=it.connection(c,value['id']);assert saved['token_hash']==security.digest(value['api_key'])
    assert client.put('/api/integrations/company',json={'name':'Test Factory'}).status_code==200
    assert client.get('/api/integrations').json()['company']=='Test Factory'

def test_manual_purchase_order_still_works(client,seeded):
    sign_in(client,seeded,'admin')
    result=client.post('/api/purchase-orders',json={'reference':'MANUAL-NEW','supplier_id':str(seeded['supplier']),'currency':'PKR','lines':[{'description':'Bags','quantity':'10','unit_price':'20'}]})
    assert result.status_code==200

def test_machine_auth_origin_cookie_scope_and_expiry(client,seeded,engine):
    ident,key=make(engine,seeded,['invoices:read'])
    client.headers['authorization']='Bearer '+key
    assert client.get('/api/integration/v1/status').status_code==403
    machine(client,key)
    assert client.get('/api/integration/v1/status').status_code==200
    assert client.post('/api/integration/v1/import/suppliers',json={'records':[SUPPLIER]}).status_code==403
    client.cookies.set(security.COOKIE,'fake')
    assert client.get('/api/integration/v1/status').status_code==403
    client.cookies.clear()
    with engine.begin() as c: db.run(c,"UPDATE integrations SET token_expires_at=now()-interval '1 second' WHERE id=:i",i=ident)
    assert client.get('/api/integration/v1/status').status_code==401

def test_key_rotation_and_disable(client,seeded,engine):
    ident,key=make(engine,seeded);sign_in(client,seeded,'admin')
    new=client.post(f'/api/integrations/{ident}/rotate-key',json={}).json()['api_key']
    machine(client,key);assert client.get('/api/integration/v1/status').status_code==401
    machine(client,new);assert client.get('/api/integration/v1/status').status_code==200
    with engine.begin() as c: db.run(c,'UPDATE integrations SET active=false WHERE id=:i',i=ident)
    assert client.get('/api/integration/v1/status').status_code==401

def test_disabled_creator_revokes_machine_access(client,seeded,engine):
    ident,key=make(engine,seeded);machine(client,key)
    with engine.begin() as c: db.run(c,"UPDATE users SET active=false WHERE username='admin'")
    assert client.get('/api/integration/v1/status').status_code==401

def test_dry_run_atomic_import_idempotency_and_no_overwrite(client,seeded,engine):
    ident,key=make(engine,seeded);machine(client,key)
    path='/api/integration/v1/import/suppliers'
    result=client.post(path,json={'records':[SUPPLIER]});assert result.status_code==200
    assert result.json()['items'][0]['status']=='would_create'
    with engine.connect() as c: assert db.run(c,'SELECT count(*) FROM integration_records').scalar_one()==0
    result=client.post(path,json={'records':[SUPPLIER],'dry_run':False});assert result.status_code==200
    again=client.post(path,json={'records':[SUPPLIER],'dry_run':False}).json()
    assert again['items'][0]['status']=='unchanged' and again['items'][0]['id']==result.json()['items'][0]['id']
    assert client.post(path,json={'records':[dict(SUPPLIER,name='Changed')],'dry_run':False}).status_code==409
    # The first new record must roll back when the second conflicts.
    batch=[{'external_id':'S-2','name':'New supplier'},dict(SUPPLIER,name='Changed')]
    assert client.post(path,json={'records':batch,'dry_run':False}).status_code==409
    with engine.connect() as c: assert db.run(c,"SELECT count(*) FROM suppliers WHERE name='New supplier'").scalar_one()==0

def test_purchase_order_and_lookup(client,seeded,engine):
    ident,key,refs=imported(engine,seeded);machine(client,key)
    result=client.get('/api/integration/v1/records/purchase_order/PO-1')
    assert result.status_code==200 and result.json()['lines'][0]['id']==refs['line']
    replay=client.post('/api/integration/v1/import/purchase_orders',json={'records':[ORDER],'dry_run':False})
    assert replay.status_code==200 and replay.json()['items'][0]['status']=='unchanged'

def test_cross_connection_import_references_rejected(engine,seeded):
    a,key,refs=imported(engine,seeded);b,_=make(engine,seeded)
    with pytest.raises(HTTPException) as exc: it.import_records(engine,b,'purchase_orders',[dict(ORDER,reference='OTHER')],False)
    assert exc.value.status_code==422

def test_import_validation(client,seeded,engine):
    ident,key=make(engine,seeded);machine(client,key)
    base='/api/integration/v1/import/'
    assert client.post(base+'suppliers',json={'records':[SUPPLIER,SUPPLIER]}).status_code==422
    assert client.post(base+'suppliers',json={'records':[dict(SUPPLIER,unexpected='x')]}).status_code==422
    assert client.post(base+'purchase_orders',json={'records':[dict(ORDER,lines=[ORDER['lines'][0]]*2)]}).status_code==422
    assert client.post(base+'purchase_orders',json={'records':[dict(ORDER,currency='XYZ')]}).status_code==422
    assert client.post(base+'payments',json={'records':[SUPPLIER]}).status_code==404

def test_csv_mapping_preview_commit(client,seeded,engine):
    ident,key=make(engine,seeded);sign_in(client,seeded,'admin')
    mapping={'suppliers':{'external_id':'Vendor Code','name':'Vendor Name'}}
    assert client.put(f'/api/integrations/{ident}',json={'active':True,'mappings':mapping}).status_code==200
    payload={'content':'Vendor Code,Vendor Name\nS-1,CSV Supplier\n'}
    path=f'/api/integrations/{ident}/csv/suppliers'
    assert client.post(path,json=payload).json()['items'][0]['status']=='would_create'
    assert client.post(path,json={**payload,'dry_run':False}).json()['items'][0]['status']=='created'
    assert client.put(f'/api/integrations/{ident}',json={'active':True,'mappings':{'suppliers':{'external_id':'Code','name':'Code'}}}).status_code==422

@pytest.mark.parametrize('csv',['external_id,name,name\n1,A,B','wrong,name\n1,A','external_id,name\n1','external_id,name\n1,A,extra','external_id,name\n'])
def test_invalid_csv(csv):
    with pytest.raises(HTTPException): it.parse_csv(csv,'suppliers',None)

def test_grouped_csv_po_fields():
    content='external_id,reference,supplier_external_id,currency,line_id,description,quantity,unit_price\nP,PO,S,PKR,1,Bags,1,2\nP,PO,S,PKR,2,Labels,3,4\n'
    parsed=it.parse_csv(content,'purchase_orders',None)
    assert len(parsed)==1 and len(parsed[0]['lines'])==2
    with pytest.raises(HTTPException): it.parse_csv(content.replace('P,PO,S,PKR,2','P,OTHER,S,PKR,2'),'purchase_orders',None)

def test_approved_invoice_scope_and_transactional_webhook(client,engine,seeded,monkeypatch):
    ident,key,refs=imported(engine,seeded);other,otherkey=make(engine,seeded)
    with engine.begin() as c: db.run(c,"UPDATE integrations SET webhook_enabled=true,webhook_url='https://erp.example.com/hook',webhook_secret=:s WHERE id=:i",i=ident,s=b'encrypted-test-secret')
    inv=invoice(engine,seeded)
    data=review_data(seeded);data['supplier_id']=refs['supplier'];data['po_id']=refs['po'];data['lines'][0]['po_line_id']=refs['line']
    services.save_review(engine,inv,schemas.ReviewRequest(**data),seeded['users']['operator'])
    machine(client,key);assert client.get('/api/integration/v1/invoices').json()['items']==[]
    services.submit(engine,inv,1,seeded['users']['operator'])
    services.decide(engine,inv,schemas.Decision(expected_version=2,decision='approve',reason='Verified'),seeded['users']['approver'])
    own=client.get('/api/integration/v1/invoices').json()['items'];assert len(own)==1 and own[0]['id']==str(inv)
    machine(client,otherkey);assert client.get('/api/integration/v1/invoices').json()['items']==[]
    assert client.get('/api/integration/v1/records/purchase_order/PO-1').status_code==404
    with engine.begin() as c:
        rows=db.rows(c,'SELECT * FROM webhook_deliveries');assert len(rows)==1 and rows[0]['integration_id']==ident
        body=json.loads(rows[0]['payload']);assert body['event']=='invoice.approved' and 'review' not in body
        event=rows[0]['event_id'];webhooks.record_event(c,event,'approved',inv)
        assert db.run(c,'SELECT count(*) FROM webhook_deliveries').scalar_one()==1

@pytest.mark.parametrize('url',['http://example.com/hook','https://127.0.0.1/hook','https://169.254.169.254/','https://10.0.0.1/','https://[::1]/','https://example.com:8443/','https://user:pass@example.com/','https://example.com/?token=secret','https://example.com/#x','https://localhost/','https://example.com/\r\nX:bad'])
def test_webhook_url_restrictions(url):
    with pytest.raises(HTTPException): webhooks.validate_url(url)

def test_dns_private_or_mixed_addresses_rejected(monkeypatch):
    monkeypatch.setattr(webhooks.socket,'getaddrinfo',lambda *args:[(2,1,6,'',('8.8.8.8',443)),(2,1,6,'',('127.0.0.1',443))])
    with pytest.raises(ValueError): webhooks.public_address('erp.example.com')

def test_pinned_transport_signed_body_and_no_redirect(monkeypatch):
    calls={}
    monkeypatch.setattr(webhooks,'public_address',lambda host:'8.8.8.8')
    class Fake:
        def __init__(self,host,ip):calls['address']=(host,ip)
        def request(self,method,path,body,headers):calls.update(method=method,path=path,body=body,headers=headers)
        def getresponse(self): return type('Response',(),{'status':302})()
        def close(self):calls['closed']=True
    monkeypatch.setattr(webhooks,'PinnedHTTPS',Fake)
    job={'id':uuid4(),'payload':'{"schema_version":1}'}
    assert webhooks.transmit('https://erp.example.com/hook',b'secret',job)==302
    stamp=calls['headers']['X-InvoiceDesk-Timestamp']
    expected=hmac.new(b'secret',stamp.encode()+b'.'+calls['body'],hashlib.sha256).hexdigest()
    assert calls['headers']['X-InvoiceDesk-Signature']=='sha256='+expected
    assert calls['address']==('erp.example.com','8.8.8.8') and calls['closed']

def delivery(engine,seeded):
    ident,key=make(engine,seeded)
    with engine.begin() as c:
        db.audit(c,None,'integration_test_event')
        event=db.run(c,'SELECT max(id) FROM audit_events').scalar_one();job=uuid4()
        db.run(c,"UPDATE integrations SET webhook_enabled=true,webhook_url='https://erp.example.com/hook',webhook_secret=:s WHERE id=:i",i=ident,s=b'test')
        db.run(c,'INSERT INTO webhook_deliveries(id,integration_id,event_id,payload) VALUES(:i,:c,:e,:p)',i=job,c=ident,e=event,p='{}')
    return ident,job

def test_worker_retry_fencing_and_exhaustion(engine,seeded,monkeypatch):
    ident,job=delivery(engine,seeded)
    monkeypatch.setattr('app.credential_store.protect',lambda value,decrypt=False:b'secret')
    monkeypatch.setattr(webhooks,'transmit',lambda *args:503)
    for i in range(3):
        assert webhooks.once(engine)
        with engine.begin() as c:
            value=db.one(c,'SELECT * FROM webhook_deliveries WHERE id=:i',i=job)
            assert value['attempts']==i+1 and value['status']==('failed' if i==2 else 'queued')
            db.run(c,"UPDATE webhook_deliveries SET available_at=now()-interval '1 second'")
    assert not webhooks.once(engine)

def test_expired_webhook_lease_cannot_finish(engine,seeded):
    ident,job=delivery(engine,seeded);first=webhooks.claim(engine)
    assert webhooks.claim(engine) is None
    with engine.begin() as c:db.run(c,"UPDATE webhook_deliveries SET lease_until=now()-interval '1 second'")
    assert not webhooks.finish(engine,first,'sent',200)
    second=webhooks.claim(engine);assert second['lease_token']!=first['lease_token']
    assert not webhooks.finish(engine,first,'sent',200)
    assert webhooks.finish(engine,second,'sent',200)

def test_worker_permanent_error_and_disable(engine,seeded,monkeypatch):
    ident,job=delivery(engine,seeded)
    monkeypatch.setattr('app.credential_store.protect',lambda value,decrypt=False:b'secret')
    monkeypatch.setattr(webhooks,'transmit',lambda *args:400)
    webhooks.once(engine)
    with engine.connect() as c:assert db.run(c,'SELECT status FROM webhook_deliveries WHERE id=:i',i=job).scalar_one()=='failed'
    ident2,job2=delivery(engine,seeded)
    with engine.begin() as c:db.run(c,'UPDATE integrations SET active=false WHERE id=:i',i=ident2)
    monkeypatch.setattr(webhooks,'transmit',lambda *args:pytest.fail('Disabled connection must not transmit'))
    webhooks.once(engine)
    with engine.connect() as c:assert db.run(c,'SELECT status FROM webhook_deliveries WHERE id=:i',i=job2).scalar_one()=='cancelled'

def test_settings_cancel_pending_and_secret_hidden(client,engine,seeded,monkeypatch):
    ident,job=delivery(engine,seeded);sign_in(client,seeded,'admin')
    monkeypatch.setattr('app.credential_store.protect',lambda value,decrypt=False:b'encrypted')
    secret=client.post(f'/api/integrations/{ident}/signing-secret',json={});assert secret.status_code==200
    assert secret.json()['signing_secret'] not in client.get('/api/integrations').text
    with engine.connect() as c:
        assert not it.connection(c,ident)['webhook_enabled']
        assert db.run(c,'SELECT status FROM webhook_deliveries WHERE id=:i',i=job).scalar_one()=='cancelled'

def test_signature_receiver_rejects_tampering_replay_and_wrong_identity():
    path=Path(__file__).parents[1]/'examples/integration/verify_webhook.py'
    spec=importlib.util.spec_from_file_location('receiver',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    ident=str(uuid4());body=json.dumps({'delivery_id':ident,'schema_version':1,'event':'invoice.approved'}).encode();stamp=str(int(time.time()))
    sig='sha256='+hmac.new(b'secret',stamp.encode()+b'.'+body,hashlib.sha256).hexdigest()
    assert module.verify('secret',body,stamp,sig,ident)['delivery_id']==ident
    for args in [('secret',body+b' ',stamp,sig,ident),('secret',body,stamp,sig,str(uuid4()))]:
        with pytest.raises(ValueError):module.verify(*args)
    with pytest.raises(ValueError):module.verify('secret',body,stamp,sig,ident,now=int(stamp)+301)
