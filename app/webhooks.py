"""Signed metadata-only webhooks with a durable outbox and public-IP pinning."""
import hashlib
import hmac
import http.client
import ipaddress
import json
import re
import secrets
import socket
import ssl
import time
from urllib.parse import urlsplit
from uuid import uuid4
from . import db
from .security import fail

def validate_url(url):
    try:
        parsed=urlsplit(url)
        valid=(parsed.scheme=='https' and parsed.hostname and parsed.port in (None,443)
               and not parsed.username and not parsed.password and not parsed.fragment and not parsed.query
               and re.fullmatch(r'[A-Za-z0-9.-]{1,253}',parsed.hostname)
               and all(32<ord(ch)<127 for ch in url) and '\\' not in url)
    except ValueError: valid=False
    if not valid: fail(422,'Use an HTTPS webhook URL on port 443 without credentials, query parameters or fragments.')
    try:
        ip=ipaddress.ip_address(parsed.hostname)
        if ip.version!=4 or not ip.is_global: fail(422,'Webhook destinations must use public IPv4 addresses.')
    except ValueError:
        if parsed.hostname.lower()=='localhost' or '.' not in parsed.hostname: fail(422,'Use a public webhook hostname.')
    return parsed

def public_address(host):
    # Deliberately IPv4-only: never connect through an IPv6 translation/tunnel prefix.
    addresses=sorted({row[4][0] for row in socket.getaddrinfo(host,443,socket.AF_INET,socket.SOCK_STREAM)})
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ValueError('Non-public webhook address.')
    return addresses[0]

class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self,host,address):
        super().__init__(host,443,timeout=8,context=ssl.create_default_context())
        self.address=address
    def connect(self):
        # Connect only to the checked IP. Preserve the hostname for SNI and certificate validation.
        raw=socket.create_connection((self.address,443),timeout=self.timeout)
        try: self.sock=self._context.wrap_socket(raw,server_hostname=self.host)
        except Exception: raw.close();raise

def transmit(url,secret,delivery):
    parsed=validate_url(url);address=public_address(parsed.hostname)
    payload=delivery['payload'].encode('utf-8');stamp=str(int(time.time()))
    signature=hmac.new(secret,stamp.encode()+b'.'+payload,hashlib.sha256).hexdigest()
    connection=PinnedHTTPS(parsed.hostname,address)
    try:
        connection.request('POST',parsed.path or '/',body=payload,headers={
            'Content-Type':'application/json','User-Agent':'InvoiceDesk-Webhooks/1',
            'X-InvoiceDesk-Delivery':str(delivery['id']),
            'X-InvoiceDesk-Timestamp':stamp,'X-InvoiceDesk-Signature':'sha256='+signature,
        })
        response=connection.getresponse()
        return response.status  # No redirects or response body processing.
    finally: connection.close()

def record_event(c,event,action,invoice_id):
    if action not in ('approved','rejected','exported') or invoice_id is None: return
    rows=db.rows(c,"SELECT x.id,r.external_id FROM integrations x JOIN integration_records r ON r.integration_id=x.id AND r.kind='purchase_order' JOIN invoices i ON CAST(i.review->>'po_id' AS uuid)=r.local_id JOIN users u ON u.id=x.created_by WHERE i.id=:i AND x.active AND x.webhook_enabled AND x.scopes @> '[\"invoices:read\"]'::jsonb AND u.active AND u.role='admin'",i=invoice_id)
    for row in rows:
        ident=uuid4()
        payload=json.dumps({'schema_version':1,'delivery_id':str(ident),'event':'invoice.'+action,'invoice_id':str(invoice_id),'purchase_order_external_id':row['external_id']},sort_keys=True,separators=(',',':'))
        db.run(c,'INSERT INTO webhook_deliveries(id,integration_id,event_id,payload) VALUES(:i,:c,:e,:p) ON CONFLICT(integration_id,event_id) DO NOTHING',i=ident,c=row['id'],e=event,p=payload)

def claim(engine):
    with engine.begin() as c:
        db.run(c,"UPDATE webhook_deliveries SET status='failed',error='Delivery attempts exhausted.',lease_token=NULL,lease_until=NULL WHERE attempts>=3 AND (status='queued' OR (status='sending' AND lease_until<now()))")
        row=db.one(c,"SELECT id FROM webhook_deliveries WHERE attempts<3 AND ((status='queued' AND available_at<=now()) OR (status='sending' AND lease_until<now())) ORDER BY available_at FOR UPDATE SKIP LOCKED LIMIT 1")
        if not row: return None
        return dict(db.one(c,"UPDATE webhook_deliveries SET status='sending',attempts=attempts+1,lease_token=:t,lease_until=now()+interval '3 minutes' WHERE id=:i RETURNING *",i=row['id'],t=uuid4()))

def finish(engine,job,status,code=None,error=None):
    with engine.begin() as c:
        return db.run(c,"UPDATE webhook_deliveries SET status=:s,response_code=:code,error=:e,available_at=now()+(:delay*interval '1 second'),lease_token=NULL,lease_until=NULL WHERE id=:i AND status='sending' AND lease_token=:t AND lease_until>clock_timestamp() RETURNING id",s=status,code=code,e=error,delay=60*job['attempts'],i=job['id'],t=job['lease_token']).scalar_one_or_none() is not None

def once(engine):
    job=claim(engine)
    if not job:return False
    with engine.connect() as c:
        conn=db.one(c,"SELECT x.* FROM integrations x JOIN users u ON u.id=x.created_by WHERE x.id=:i AND x.active AND x.webhook_enabled AND u.active AND u.role='admin'",i=job['integration_id'])
    if not conn or not conn['webhook_secret'] or 'invoices:read' not in conn['scopes']:
        finish(engine,job,'cancelled');return True
    code=None
    try:
        from .credential_store import protect
        secret=protect(bytes(conn['webhook_secret']),decrypt=True)
        code=transmit(conn['webhook_url'],secret,job)
        if 200<=code<300:finish(engine,job,'sent',code);return True
        retryable=code in (408,429) or code>=500
    except Exception: retryable=True
    status='queued' if retryable and job['attempts']<3 else 'failed'
    finish(engine,job,status,code,'Webhook delivery failed. Check destination availability and signing configuration.')
    return True
