import hashlib
import secrets
import time
import json
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import Request, HTTPException
from starlette.responses import JSONResponse
from . import db
from .config import MAX_UPLOAD

passwords=PasswordHasher(time_cost=3,memory_cost=65536,parallelism=2)
DUMMY=passwords.hash(secrets.token_urlsafe(32))
COOKIE='invoicedesk_session'
def digest(s): return hashlib.sha256(s.encode()).hexdigest()
def csrf(s): return digest('invoicedesk-csrf:'+s)
def fail(code,msg): raise HTTPException(code,msg)
def rate(engine,key,maximum,seconds=60):
    with engine.begin() as c:
        hits=db.run(c,'INSERT INTO rate_limits(key,bucket,hits) VALUES(:k,:b,1) ON CONFLICT(key,bucket) DO UPDATE SET hits=rate_limits.hits+1 RETURNING hits',k=digest(key),b=int(time.time())//seconds).scalar_one()
    if hits>maximum: fail(429,'Request limit reached. Please wait and retry.')
def authenticate(request: Request):
    token=request.cookies.get(COOKIE,'')
    if not token or len(token)>128: fail(401,'Please sign in.')
    with request.app.state.engine.begin() as c:
        u=db.one(c,'SELECT u.id,u.username,u.role FROM users u JOIN sessions s ON u.id=s.user_id WHERE s.token_hash=:t AND s.expires_at>now() AND u.active',t=digest(token))
    if not u: fail(401,'Session expired. Please sign in again.')
    if request.method not in ('GET','HEAD','OPTIONS') and not secrets.compare_digest(request.headers.get('x-csrf-token',''),csrf(token)):
        fail(403,'Invalid session verification token. Reload the page.')
    rate(request.app.state.engine,'user:'+str(u['id']),120)
    return dict(u)
def require(u,*roles):
    if u['role'] not in roles: fail(403,'Your role cannot perform this action.')
def login(engine,body,ip):
    rate(engine,'login-ip:'+ip,20); rate(engine,'login-name:'+body.username.casefold(),5)
    with engine.begin() as c:
        u=db.one(c,'SELECT * FROM users WHERE username=:n',n=body.username.casefold())
        try: valid=passwords.verify(u['password_hash'] if u else DUMMY,body.password)
        except (VerificationError,InvalidHashError): valid=False
        if not valid or not u or not u['active']: fail(401,'Username or password is incorrect.')
        db.run(c,'DELETE FROM sessions WHERE expires_at<now()')
        token=secrets.token_urlsafe(32)
        db.run(c,"INSERT INTO sessions VALUES(:t,:u,now()+interval '1 hour')",t=digest(token),u=u['id'])
        db.audit(c,u['id'],'signed_in')
    return token
class Boundary:
    def __init__(self,app): self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http': return await self.app(scope,receive,send)
        h={k.decode().lower():v.decode() for k,v in scope['headers']}; rid=secrets.token_hex(8)
        async def guarded(message):
            if message['type']=='http.response.start':
                message['headers'] += [(b'cache-control',b'no-store'),(b'x-content-type-options',b'nosniff'),(b'x-frame-options',b'DENY'),(b'referrer-policy',b'no-referrer'),(b'x-request-id',rid.encode()),(b'content-security-policy',b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")]
            await send(message)
        async def reject(code,msg): return await JSONResponse({'error':msg,'request_id':rid},status_code=code)(scope,receive,guarded)
        if h.get('host') not in ('127.0.0.1:8030','localhost:8030'): return await reject(400,'Invalid host.')
        if scope['method'] not in ('GET','HEAD','OPTIONS'):
            machine=scope['path'].startswith('/api/integration/v1/')
            if machine:
                if h.get('origin') or not h.get('authorization','').startswith('Bearer idk_'): return await reject(403,'Use an authenticated server-side integration client.')
            elif h.get('origin') not in ('http://127.0.0.1:8030','http://localhost:8030'): return await reject(403,'This action must originate from this application.')
            media=h.get('content-type','').split(';')[0]
            allowed=('multipart/form-data',) if scope['path']=='/api/invoices' else ('application/json',)
            if media not in allowed: return await reject(415,'Unsupported request content type.')
        limit=MAX_UPLOAD+65536 if scope['path']=='/api/invoices' else 131072
        data=bytearray()
        while True:
            event=await receive()
            if event['type']=='http.disconnect': return
            data.extend(event.get('body',b''))
            if len(data)>limit: return await reject(413,'Request exceeds the size limit.')
            if not event.get('more_body',False): break
        consumed=False
        async def replay():
            nonlocal consumed
            if not consumed:
                consumed=True; return {'type':'http.request','body':bytes(data),'more_body':False}
            return await receive()
        # Keep unexpected failures within the same no-store/CSP boundary.
        started=False
        async def tracked(message):
            nonlocal started
            if message['type']=='http.response.start': started=True
            await guarded(message)
        try: await self.app(scope,replay,tracked)
        except Exception as exc:
            if started: raise
            print(json.dumps({'event':'request_failed','request_id':rid,'error_type':type(exc).__name__,'method':scope['method']}),flush=True)
            await reject(500,'The request could not be completed. Contact the administrator with this request reference.')
