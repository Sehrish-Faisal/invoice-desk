import json
from sqlalchemy import create_engine, text
from .config import database_url

def engine(url=None):
    return create_engine(url or database_url(), pool_pre_ping=True, pool_size=8, max_overflow=4, hide_parameters=True,
                         connect_args={'connect_timeout':5,'options':'-c statement_timeout=15000 -c lock_timeout=5000'})
def run(c, sql, /, **params): return c.execute(text(sql), params)
def one(c, sql, /, **params): return run(c, sql, **params).mappings().first()
def rows(c, sql, /, **params): return [dict(r) for r in run(c, sql, **params).mappings()]
def audit(c, actor, action, ident=None, **details):
    event=run(c, 'INSERT INTO audit_events(actor_id,action,entity_id,details) VALUES(:a,:b,:i,CAST(:d AS jsonb)) RETURNING id',
        a=actor,b=action,i=ident,d=json.dumps(details)).scalar_one()
    from .notifications import record_event
    record_event(c,event,action,ident)
    from .webhooks import record_event as webhook_event
    webhook_event(c,event,action,ident)
