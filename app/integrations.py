"""Versioned connector API for one company per deployment.

External identifiers belong to one connection. Imports are immutable and
idempotent: identical replay succeeds, conflicting replay never overwrites a PO.
"""
import csv
import io
import json
import secrets
from typing import Literal
from uuid import UUID,uuid4
from fastapi import APIRouter,Depends,Request,Query
from pydantic import Field,StrictBool,ValidationError
from . import db,security as sec,schemas as s

router=APIRouter()
SCOPES={'reference:write','invoices:read'}
FIELDS={
    'suppliers':['external_id','name'],
    'purchase_orders':['external_id','reference','supplier_external_id','currency','line_id','description','quantity','unit_price'],
}
class Company(s.Model):
    name:str=Field(min_length=2,max_length=150)
class Connection(s.Model):
    name:str=Field(min_length=2,max_length=80)
    scopes:list[Literal['reference:write','invoices:read']]=Field(min_length=1,max_length=2)
class Settings(s.Model):
    active:StrictBool
    mappings:dict[str,dict[str,str]]=Field(default_factory=dict)
    webhook_url:str=Field(default='',max_length=2048)
    webhook_enabled:StrictBool=False
class SupplierRecord(s.Supplier):
    external_id:str=Field(min_length=1,max_length=120)
class OrderLine(s.POLine):
    external_id:str=Field(min_length=1,max_length=120)
class OrderRecord(s.Model):
    external_id:str=Field(min_length=1,max_length=120)
    reference:str=Field(min_length=1,max_length=80)
    supplier_external_id:str=Field(min_length=1,max_length=120)
    currency:Literal['PKR','USD','EUR','GBP']
    lines:list[OrderLine]=Field(min_length=1,max_length=100)
class ImportBatch(s.Model):
    records:list[dict]=Field(min_length=1,max_length=100)
    dry_run:StrictBool=True
class CSVImport(s.Model):
    content:str=Field(min_length=1,max_length=100000)
    dry_run:StrictBool=True

def admin(u=Depends(sec.authenticate)):
    sec.require(u,'admin');return u

def connection(c,ident,lock=False):
    row=db.one(c,'SELECT * FROM integrations WHERE id=:i'+(' FOR UPDATE' if lock else ''),i=ident)
    if not row: sec.fail(404,'Connection not found.')
    return row

def authenticate(request:Request):
    # Never accept a browser session as a machine identity.
    if request.headers.get('origin') or request.cookies.get(sec.COOKIE): sec.fail(403,'Use a server-side API client without browser cookies.')
    value=request.headers.get('authorization','')
    if not value.startswith('Bearer idk_') or len(value)>160: sec.fail(401,'A valid integration API key is required.')
    sec.rate(request.app.state.engine,'integration-ip:'+request.client.host,120)
    with request.app.state.engine.connect() as c:
        row=db.one(c,"SELECT i.* FROM integrations i JOIN users u ON u.id=i.created_by WHERE i.token_hash=:h AND i.active AND i.token_expires_at>now() AND u.active AND u.role='admin'",h=sec.digest(value[7:]))
    if not row: sec.fail(401,'Integration key is invalid, expired or revoked.')
    sec.rate(request.app.state.engine,'integration:'+str(row['id']),120)
    return dict(row)

def scope(conn,name):
    if name not in conn['scopes']: sec.fail(403,'This connection does not have the required permission.')

@router.get('/api/integrations')
def list_connections(request:Request,u=Depends(admin)):
    with request.app.state.engine.connect() as c:
        return {'company':db.run(c,'SELECT name FROM company_settings WHERE id=1').scalar_one(),
                'items':db.rows(c,'SELECT id,name,active,scopes,token_expires_at,mappings,webhook_url,webhook_enabled,created_at FROM integrations ORDER BY created_at'),
                'fields':FIELDS}

@router.put('/api/integrations/company')
def company(body:Company,request:Request,u=Depends(admin)):
    with request.app.state.engine.begin() as c:
        db.run(c,'UPDATE company_settings SET name=:n WHERE id=1',n=body.name)
        db.audit(c,u['id'],'company_settings_updated')
    return {'ok':True}

@router.post('/api/integrations')
def create(body:Connection,request:Request,u=Depends(admin)):
    key='idk_'+secrets.token_urlsafe(32);ident=uuid4()
    with request.app.state.engine.begin() as c:
        db.run(c,"INSERT INTO integrations(id,name,created_by,scopes,token_hash,token_expires_at) VALUES(:i,:n,:u,CAST(:s AS jsonb),:h,now()+interval '90 days')",i=ident,n=body.name,u=u['id'],s=json.dumps(sorted(set(body.scopes))),h=sec.digest(key))
        db.audit(c,u['id'],'integration_created',ident)
    return {'id':ident,'api_key':key,'expires_in_days':90}

@router.post('/api/integrations/{ident}/rotate-key')
def rotate(ident:UUID,request:Request,u=Depends(admin)):
    key='idk_'+secrets.token_urlsafe(32)
    with request.app.state.engine.begin() as c:
        connection(c,ident,True)
        db.run(c,"UPDATE integrations SET token_hash=:h,token_expires_at=now()+interval '90 days' WHERE id=:i",h=sec.digest(key),i=ident)
        db.audit(c,u['id'],'integration_key_rotated',ident)
    return {'api_key':key,'expires_in_days':90}

@router.put('/api/integrations/{ident}')
def update(ident:UUID,body:Settings,request:Request,u=Depends(admin)):
    from .webhooks import validate_url
    for kind,mapping in body.mappings.items():
        if kind not in FIELDS or set(mapping)!=set(FIELDS[kind]) or len(set(mapping.values()))!=len(mapping) or any(not v or len(v)>120 for v in mapping.values()):
            sec.fail(422,'Map each required field to a unique CSV column name.')
    if body.webhook_url: validate_url(body.webhook_url)
    if body.webhook_enabled and not body.webhook_url: sec.fail(422,'Enter an HTTPS webhook URL before enabling delivery.')
    with request.app.state.engine.begin() as c:
        old=connection(c,ident,True)
        if body.webhook_enabled and not old['webhook_secret']: sec.fail(409,'Generate and save a signing secret before enabling webhooks.')
        db.run(c,'UPDATE integrations SET active=:a,mappings=CAST(:m AS jsonb),webhook_url=:w,webhook_enabled=:e WHERE id=:i',a=body.active,m=json.dumps(body.mappings),w=body.webhook_url,e=body.webhook_enabled,i=ident)
        if not body.active or not body.webhook_enabled or old['webhook_url']!=body.webhook_url:
            db.run(c,"UPDATE webhook_deliveries SET status='cancelled',lease_token=NULL,lease_until=NULL WHERE integration_id=:i AND status IN ('queued','sending')",i=ident)
        db.audit(c,u['id'],'integration_settings_updated',ident)
    return {'ok':True}

@router.post('/api/integrations/{ident}/signing-secret')
def signing_secret(ident:UUID,request:Request,u=Depends(admin)):
    from .credential_store import protect
    secret=secrets.token_urlsafe(32);encrypted=protect(secret.encode())
    with request.app.state.engine.begin() as c:
        connection(c,ident,True)
        db.run(c,'UPDATE integrations SET webhook_secret=:s,webhook_enabled=false WHERE id=:i',s=encrypted,i=ident)
        db.run(c,"UPDATE webhook_deliveries SET status='cancelled',lease_token=NULL,lease_until=NULL WHERE integration_id=:i AND status IN ('queued','sending')",i=ident)
        db.audit(c,u['id'],'webhook_secret_rotated',ident)
    return {'signing_secret':secret,'message':'Save this secret in the receiving system, then enable webhook delivery.'}

def parse_records(kind,records):
    model={'suppliers':SupplierRecord,'purchase_orders':OrderRecord}.get(kind)
    if not model: sec.fail(404,'Import type not supported.')
    parsed=[]
    for number,record in enumerate(records,1):
        try: value=model.model_validate(record)
        except ValidationError as exc:
            fields=', '.join('.'.join(map(str,e['loc'])) for e in exc.errors())
            sec.fail(422,f'Record {number}: check required fields and formats ({fields}).')
        if kind=='purchase_orders' and len({line.external_id for line in value.lines})!=len(value.lines): sec.fail(422,f'Record {number}: line IDs must be unique.')
        parsed.append(value)
    if len({r.external_id for r in parsed})!=len(parsed): sec.fail(422,'External IDs must be unique within a batch.')
    return parsed

def import_records(engine,ident,kind,records,dry_run,actor=None):
    parsed=parse_records(kind,records);result=[]
    with engine.connect() as c:
        tx=c.begin()
        try:
            conn=connection(c,ident,True)  # serialize imports for this source
            if not conn['active']: sec.fail(409,'Enable this connection before importing.')
            scope(conn,'reference:write')
            for record in parsed:
                value=record.model_dump(mode='json');fingerprint=sec.digest(json.dumps(value,sort_keys=True,separators=(',',':')))
                record_kind='supplier' if kind=='suppliers' else 'purchase_order'
                existing=db.one(c,'SELECT * FROM integration_records WHERE integration_id=:i AND kind=:k AND external_id=:e',i=ident,k=record_kind,e=record.external_id)
                if existing:
                    if existing['content_hash']!=fingerprint: sec.fail(409,'External ID already exists with different data. Imports never overwrite existing purchase references.')
                    result.append({'external_id':record.external_id,'id':str(existing['local_id']),'status':'unchanged'});continue
                local=uuid4()
                if kind=='suppliers':
                    db.run(c,'INSERT INTO suppliers(id,name) VALUES(:i,:n)',i=local,n=record.name)
                else:
                    supplier=db.one(c,"SELECT local_id FROM integration_records WHERE integration_id=:i AND kind='supplier' AND external_id=:e",i=ident,e=record.supplier_external_id)
                    if not supplier: sec.fail(422,'Import the supplier into this connection before its purchase order.')
                    db.run(c,'INSERT INTO purchase_orders(id,reference,supplier_id,currency) VALUES(:i,:r,:s,:c)',i=local,r=record.reference,s=supplier['local_id'],c=record.currency)
                    for line in record.lines:
                        # Deterministic line IDs are returned through the record lookup.
                        from uuid import uuid5
                        db.run(c,'INSERT INTO po_lines(id,po_id,description,quantity,unit_price) VALUES(:i,:p,:d,:q,:u)',i=uuid5(local,line.external_id),p=local,d=line.description,q=line.quantity,u=line.unit_price)
                db.run(c,'INSERT INTO integration_records VALUES(:i,:k,:e,:l,:h)',i=ident,k=record_kind,e=record.external_id,l=local,h=fingerprint)
                result.append({'external_id':record.external_id,'id':None if dry_run else str(local),'status':'would_create' if dry_run else 'created'})
            if dry_run: tx.rollback()
            else:
                db.audit(c,actor,'integration_imported',ident,kind=kind,count=len(result));tx.commit()
        except Exception: tx.rollback();raise
    return {'dry_run':dry_run,'items':result}

def parse_csv(content,kind,mapping):
    if kind not in FIELDS: sec.fail(404,'Import type not supported.')
    mapping=mapping or {k:k for k in FIELDS[kind]}
    try:
        reader=csv.DictReader(io.StringIO(content.lstrip('\ufeff')),strict=True)
        headers=reader.fieldnames or []
        if len(headers)!=len(set(headers)) or not set(mapping.values()).issubset(headers): sec.fail(422,'CSV headers must be unique and include all mapped columns.')
        rows=[]
        for index,row in enumerate(reader,2):
            if index>501: sec.fail(422,'Import at most 500 CSV rows at a time.')
            if None in row or any(row.get(v) is None for v in mapping.values()): sec.fail(422,f'CSV row {index} has a missing or extra column.')
            rows.append({k:row[v].strip() for k,v in mapping.items()})
    except (csv.Error,UnicodeError): sec.fail(422,'CSV could not be parsed. Use UTF-8 comma-separated data.')
    if not rows: sec.fail(422,'CSV has no data rows.')
    if kind=='suppliers': records=rows
    else:
        grouped={}
        for row in rows:
            base={k:row[k] for k in ['external_id','reference','supplier_external_id','currency']}
            if row['external_id'] not in grouped: grouped[row['external_id']]={**base,'lines':[]}
            order=grouped[row['external_id']]
            if any(order[k]!=v for k,v in base.items()): sec.fail(422,'Rows for one purchase order must have matching order fields.')
            order['lines'].append({'external_id':row['line_id'],**{k:row[k] for k in ['description','quantity','unit_price']}})
        records=list(grouped.values())
    if len(records)>100: sec.fail(422,'Import at most 100 records per batch.')
    return records

@router.post('/api/integrations/{ident}/csv/{kind}')
def csv_import(ident:UUID,kind:str,body:CSVImport,request:Request,u=Depends(admin)):
    with request.app.state.engine.connect() as c: conn=connection(c,ident)
    records=parse_csv(body.content,kind,conn['mappings'].get(kind))
    return import_records(request.app.state.engine,ident,kind,records,body.dry_run,u['id'])

@router.get('/api/integration/v1/status')
def status(conn=Depends(authenticate)):
    return {'api_version':'1','connection_id':conn['id'],'name':conn['name'],'scopes':conn['scopes'],'expires_at':conn['token_expires_at']}

@router.post('/api/integration/v1/import/{kind}')
def machine_import(kind:str,body:ImportBatch,request:Request,conn=Depends(authenticate)):
    scope(conn,'reference:write')
    return import_records(request.app.state.engine,conn['id'],kind,body.records,body.dry_run)

@router.get('/api/integration/v1/records/{kind}/{external_id}')
def record_lookup(kind:str,external_id:str,request:Request,conn=Depends(authenticate)):
    scope(conn,'reference:write')
    with request.app.state.engine.connect() as c:
        row=db.one(c,'SELECT local_id FROM integration_records WHERE integration_id=:i AND kind=:k AND external_id=:e',i=conn['id'],k=kind,e=external_id)
        if not row: sec.fail(404,'External record not found.')
        return {'id':row['local_id'],'lines':db.rows(c,'SELECT id,description,quantity,unit_price FROM po_lines WHERE po_id=:i ORDER BY id',i=row['local_id']) if kind=='purchase_order' else []}

@router.get('/api/integration/v1/invoices')
def approved_invoices(request:Request,offset:int=Query(0,ge=0),conn=Depends(authenticate)):
    scope(conn,'invoices:read')
    with request.app.state.engine.connect() as c:
        # One connection can only retrieve invoices referencing its own imported POs.
        return {'items':db.rows(c,"SELECT i.id,i.state,i.version,i.review,r.external_id AS purchase_order_external_id FROM invoices i JOIN integration_records r ON r.local_id=CAST(i.review->>'po_id' AS uuid) AND r.kind='purchase_order' WHERE r.integration_id=:c AND i.state IN ('approved','exported') ORDER BY i.created_at,i.id LIMIT 100 OFFSET :o",c=conn['id'],o=offset)}

@router.get('/api/integrations/{ident}/deliveries')
def deliveries(ident:UUID,request:Request,u=Depends(admin)):
    with request.app.state.engine.connect() as c:
        connection(c,ident)
        return db.rows(c,'SELECT id,status,attempts,response_code,error,created_at FROM webhook_deliveries WHERE integration_id=:i ORDER BY created_at DESC LIMIT 100',i=ident)

@router.post('/api/integrations/{ident}/deliveries/{delivery_id}/retry')
def retry(ident:UUID,delivery_id:UUID,request:Request,u=Depends(admin)):
    with request.app.state.engine.begin() as c:
        conn=connection(c,ident,True)
        if not conn['active'] or not conn['webhook_enabled']: sec.fail(409,'Enable this connection and its webhook first.')
        changed=db.run(c,"UPDATE webhook_deliveries SET status='queued',attempts=0,available_at=now(),error=NULL WHERE id=:d AND integration_id=:i AND status='failed' RETURNING id",d=delivery_id,i=ident).scalar_one_or_none()
        if not changed: sec.fail(409,'Only failed deliveries can be retried.')
        db.audit(c,u['id'],'webhook_retry_requested',ident,delivery_id=str(delivery_id))
    return {'ok':True}
