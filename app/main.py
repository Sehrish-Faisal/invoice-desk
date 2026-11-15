import hashlib
import json
import shutil
from contextlib import asynccontextmanager
from uuid import UUID, uuid4
from fastapi import FastAPI, Depends, Request, UploadFile, File, Form, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, StrictBool
from sqlalchemy.exc import IntegrityError
from . import db, security as sec, schemas as s, services
from .config import ROOT, STATE, MAX_UPLOAD
from .documents import decode

@asynccontextmanager
async def lifespan(app):
    if not hasattr(app.state,'engine'): app.state.engine=db.engine()
    yield
    app.state.engine.dispose()
app=FastAPI(title='InvoiceDesk',docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
app.add_middleware(sec.Boundary)
@app.exception_handler(HTTPException)
async def http_error(request,exc): return JSONResponse({'error':exc.detail},status_code=exc.status_code)
@app.exception_handler(RequestValidationError)
async def validation(request,exc):
    fields=list(dict.fromkeys('.'.join(str(v) for v in e['loc'][1:]) for e in exc.errors()))
    return JSONResponse({'error':'Check the required fields and numeric formats.','fields':fields},status_code=422)
@app.exception_handler(IntegrityError)
async def conflict(request,exc): return JSONResponse({'error':'A duplicate record or invalid reference was detected. Reload and check your entries.'},status_code=409)
@app.exception_handler(Exception)
async def unexpected(request,exc): return JSONResponse({'error':'The request could not be completed. Retry or contact the administrator.'},status_code=500)
@app.get('/health')
def health(): return {'status':'ok'}
@app.get('/ready')
def ready(request:Request):
    with request.app.state.engine.connect() as c: db.run(c,'SELECT 1')
    return {'status':'ready'}
@app.post('/api/auth/login')
def login(body:s.Login,request:Request):
    token=sec.login(request.app.state.engine,body,request.client.host)
    response=JSONResponse({'ok':True}); response.set_cookie(sec.COOKIE,token,max_age=3600,httponly=True,samesite='strict',path='/')
    return response
@app.post('/api/auth/logout')
def logout(request:Request,u=Depends(sec.authenticate)):
    with request.app.state.engine.begin() as c: db.run(c,'DELETE FROM sessions WHERE token_hash=:t',t=sec.digest(request.cookies[sec.COOKIE]))
    r=JSONResponse({'ok':True}); r.delete_cookie(sec.COOKIE,path='/'); return r
@app.get('/api/session')
def session(request:Request,u=Depends(sec.authenticate)):
    return {**u,'csrf':sec.csrf(request.cookies[sec.COOKIE]),'ai_configured':(STATE/'ai-credentials.dpapi').exists()}
@app.get('/api/reference')
def reference(request:Request,u=Depends(sec.authenticate)):
    with request.app.state.engine.connect() as c:
        return {'suppliers':db.rows(c,'SELECT * FROM suppliers ORDER BY name LIMIT 1000'),
                'orders':db.rows(c,'SELECT * FROM purchase_orders ORDER BY reference LIMIT 1000'),
                'lines':db.rows(c,'SELECT p.*,COALESCE((SELECT sum(a.quantity) FROM allocations a WHERE a.po_line_id=p.id),0) AS billed FROM po_lines p ORDER BY description LIMIT 10000')}
@app.post('/api/suppliers')
def supplier(body:s.Supplier,request:Request,u=Depends(sec.authenticate)):
    sec.require(u,'admin'); ident=uuid4()
    with request.app.state.engine.begin() as c:
        db.run(c,'INSERT INTO suppliers VALUES(:i,:n)',i=ident,n=body.name); db.audit(c,u['id'],'supplier_created',ident)
    return {'id':ident}
@app.post('/api/purchase-orders')
def po(body:s.PurchaseOrder,request:Request,u=Depends(sec.authenticate)):
    sec.require(u,'admin'); ident=uuid4()
    with request.app.state.engine.begin() as c:
        db.run(c,'INSERT INTO purchase_orders VALUES(:i,:r,:s,:c)',i=ident,r=body.reference,s=body.supplier_id,c=body.currency)
        for line in body.lines: db.run(c,'INSERT INTO po_lines VALUES(:i,:p,:d,:q,:u)',i=uuid4(),p=ident,d=line.description,q=line.quantity,u=line.unit_price)
        db.audit(c,u['id'],'purchase_order_created',ident)
    return {'id':ident}
@app.post('/api/invoices')
def upload(request:Request,file:UploadFile=File(...),consent:bool=Form(False),u=Depends(sec.authenticate)):
    sec.require(u,'admin','operator'); sec.rate(request.app.state.engine,'upload:'+str(u['id']),10)
    data=file.file.read(MAX_UPLOAD+1)
    if not data or len(data)>MAX_UPLOAD: sec.fail(413,'Choose a document between 1 byte and 10 MiB.')
    ident=uuid4(); folder=STATE/'documents'/str(ident); folder.mkdir(parents=True)
    try:
        original=folder/'original'; original.write_bytes(data); metadata=decode(original,folder/'pages')
        with request.app.state.engine.begin() as c:
            db.run(c,'INSERT INTO invoices(id,sha256,media_type,page_count,uploader_id) VALUES(:i,:h,:m,:p,:u)',i=ident,h=hashlib.sha256(data).hexdigest(),m=metadata['media_type'],p=metadata['page_count'],u=u['id'])
            if consent: db.run(c,'INSERT INTO jobs(id,invoice_id) VALUES(:j,:i)',j=uuid4(),i=ident)
            db.audit(c,u['id'],'invoice_uploaded',ident,cloud_consent=consent)
    except Exception:
        shutil.rmtree(folder); raise
    return {'id':ident,'state':'needs_review','extraction':'queued' if consent else 'manual'}
@app.get('/api/invoices')
def invoices(request:Request,offset:int=Query(0,ge=0),limit:int=Query(30,ge=1,le=100),u=Depends(sec.authenticate)):
    with request.app.state.engine.connect() as c:
        return {'items':db.rows(c,"SELECT i.id,i.state,i.version,i.created_at,i.review->>'invoice_number' AS invoice_number,i.review->>'declared_total' AS total,i.review->>'currency' AS currency,s.name AS supplier,j.status AS extraction FROM invoices i LEFT JOIN jobs j ON j.invoice_id=i.id LEFT JOIN suppliers s ON s.id=CAST(i.review->>'supplier_id' AS uuid) ORDER BY i.created_at DESC LIMIT :l OFFSET :o",l=limit,o=offset),
                'counts':db.rows(c,'SELECT state,count(*) AS count FROM invoices GROUP BY state')}
@app.get('/api/invoices/{ident}')
def detail(ident:UUID,request:Request,u=Depends(sec.authenticate)):
    with request.app.state.engine.connect() as c:
        inv=db.one(c,'SELECT * FROM invoices WHERE id=:i',i=ident)
        if not inv: sec.fail(404,'Invoice not found.')
        inv=dict(inv); inv.pop('sha256')
        inv['job']=db.one(c,'SELECT status,attempts,error FROM jobs WHERE invoice_id=:i',i=ident)
        inv['suggestion']=db.one(c,'SELECT a.suggestion,a.model,a.elapsed_ms FROM extraction_attempts a JOIN jobs j ON j.id=a.job_id WHERE j.invoice_id=:i AND a.suggestion IS NOT NULL ORDER BY a.created_at DESC LIMIT 1',i=ident)
        inv['history']=db.rows(c,'SELECT a.action,a.created_at,a.details,u.username FROM audit_events a LEFT JOIN users u ON u.id=a.actor_id WHERE entity_id=:i ORDER BY a.id',i=ident)
        inv['issues']=services.issues(c,s.Review.model_validate(inv['review'])) if inv['review'] and inv['state'] in ('needs_review','submitted','rejected') else []
        return inv
@app.get('/api/invoices/{ident}/pages/{page}')
def preview(ident:UUID,page:int,request:Request,u=Depends(sec.authenticate)):
    with request.app.state.engine.connect() as c: inv=db.one(c,'SELECT page_count FROM invoices WHERE id=:i',i=ident)
    if not inv or not 1<=page<=inv['page_count']: sec.fail(404,'Page not found.')
    return FileResponse(STATE/'documents'/str(ident)/'pages'/f'{page}.png',media_type='image/png')
@app.put('/api/invoices/{ident}/review')
def review(ident:UUID,body:s.ReviewRequest,request:Request,u=Depends(sec.authenticate)):
    return services.save_review(request.app.state.engine,ident,body,u)
@app.post('/api/invoices/{ident}/submit')
def submit(ident:UUID,body:s.Version,request:Request,u=Depends(sec.authenticate)):
    return services.submit(request.app.state.engine,ident,body.expected_version,u)
@app.post('/api/invoices/{ident}/decision')
def decision(ident:UUID,body:s.Decision,request:Request,u=Depends(sec.authenticate)):
    return services.decide(request.app.state.engine,ident,body,u)
@app.post('/api/invoices/{ident}/export')
def export(ident:UUID,request:Request,u=Depends(sec.authenticate)):
    value=services.export_invoice(request.app.state.engine,ident,u)
    return Response(value['csv'],media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="invoice-{value["id"]}.csv"'})
@app.get('/api/audit')
def audit(request:Request,u=Depends(sec.authenticate)):
    with request.app.state.engine.connect() as c: return db.rows(c,'SELECT a.id,a.action,a.entity_id,a.created_at,a.details,u.username FROM audit_events a LEFT JOIN users u ON u.id=a.actor_id ORDER BY a.id DESC LIMIT 100')
@app.get('/draft.mjs')
def draft_module():
    # Windows registry MIME mappings may incorrectly classify .mjs as text/plain.
    return FileResponse(ROOT/'static/draft.mjs',media_type='application/javascript')

class NotificationPreference(BaseModel):
    email_enabled: StrictBool

@app.get('/api/notifications')
def notification_inbox(request:Request,offset:int=Query(0,ge=0),u=Depends(sec.authenticate)):
    with request.app.state.engine.connect() as c:
        return {'items':db.rows(c,'SELECT n.id,n.invoice_id,n.title,n.created_at,n.read_at,e.status AS email_status FROM notifications n LEFT JOIN notification_emails e ON e.notification_id=n.id WHERE n.user_id=:u ORDER BY n.created_at DESC,n.id DESC LIMIT 30 OFFSET :o',u=u['id'],o=offset),
                'total':db.run(c,'SELECT count(*) FROM notifications WHERE user_id=:u',u=u['id']).scalar_one(),
                'unread':db.run(c,'SELECT count(*) FROM notifications WHERE user_id=:u AND read_at IS NULL',u=u['id']).scalar_one()}

@app.get('/api/notifications/preferences')
def notification_preferences(request:Request,u=Depends(sec.authenticate)):
    from .notifications import settings
    try: configured=settings() is not None
    except Exception: configured=False
    with request.app.state.engine.connect() as c:
        contact=db.one(c,'SELECT email,email_enabled FROM notification_contacts WHERE user_id=:u',u=u['id'])
    return {'smtp_configured':configured,'email':contact['email'] if contact else None,'email_enabled':bool(contact and contact['email_enabled'])}

@app.put('/api/notifications/preferences')
def update_notification_preferences(body:NotificationPreference,request:Request,u=Depends(sec.authenticate)):
    with request.app.state.engine.begin() as c:
        updated=db.run(c,'UPDATE notification_contacts SET email_enabled=:v WHERE user_id=:u RETURNING user_id',v=body.email_enabled,u=u['id']).scalar_one_or_none()
        if not updated: sec.fail(409,'Ask your administrator to register your email address first.')
        if not body.email_enabled:
            db.run(c,"UPDATE notification_emails SET status='cancelled' WHERE status='queued' AND notification_id IN (SELECT id FROM notifications WHERE user_id=:u)",u=u['id'])
        db.audit(c,u['id'],'email_notification_preference_updated',email_enabled=body.email_enabled)
    return {'ok':True}

@app.post('/api/notifications/read-all')
def read_all_notifications(request:Request,u=Depends(sec.authenticate)):
    with request.app.state.engine.begin() as c:
        db.run(c,'UPDATE notifications SET read_at=now() WHERE user_id=:u AND read_at IS NULL',u=u['id'])
    return {'ok':True}

@app.post('/api/notifications/{ident}/read')
def read_notification(ident:UUID,request:Request,u=Depends(sec.authenticate)):
    with request.app.state.engine.begin() as c:
        changed=db.run(c,'UPDATE notifications SET read_at=COALESCE(read_at,now()) WHERE id=:i AND user_id=:u RETURNING id',i=ident,u=u['id']).scalar_one_or_none()
        if not changed: sec.fail(404,'Notification not found.')
    return {'ok':True}
from .integrations import router as integration_router
app.include_router(integration_router)
@app.get('/integration-guide')
def integration_guide(u=Depends(sec.authenticate)):
    sec.require(u,'admin')
    return FileResponse(ROOT/'docs/integrations.md',media_type='text/plain')
app.mount('/',StaticFiles(directory=ROOT/'static',html=True),name='static')
