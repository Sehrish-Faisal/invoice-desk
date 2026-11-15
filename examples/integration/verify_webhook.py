"""Receiver helper. Persistently deduplicate delivery_id before processing events."""
import hashlib
import hmac
import json
import time
from uuid import UUID

def verify(secret,raw_body,timestamp,signature,delivery_id,now=None):
    if len(raw_body)>16384 or not timestamp.isdigit() or len(timestamp)>12:
        raise ValueError('Invalid webhook envelope.')
    now=time.time() if now is None else now
    if abs(now-int(timestamp))>300: raise ValueError('Expired webhook timestamp.')
    expected='sha256='+hmac.new(secret.encode(),timestamp.encode()+b'.'+raw_body,hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected,signature): raise ValueError('Invalid webhook signature.')
    event=json.loads(raw_body)
    if str(UUID(delivery_id))!=event.get('delivery_id') or event.get('schema_version')!=1:
        raise ValueError('Invalid delivery identity.')
    if event.get('event') not in ('invoice.approved','invoice.rejected','invoice.exported'):
        raise ValueError('Unsupported webhook event.')
    return event
