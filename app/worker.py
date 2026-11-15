import json
import time
from uuid import uuid4
from . import db, ai, notifications, webhooks
from .config import STATE

def claim(engine):
    with engine.begin() as c:
        exhausted=db.rows(c,"UPDATE jobs SET status='failed',error='Extraction stopped after three attempts.',lease_token=NULL,lease_until=NULL WHERE attempts>=3 AND (status='queued' OR (status='running' AND lease_until<now())) RETURNING invoice_id")
        for row in exhausted: db.audit(c,None,'extraction_failed',row['invoice_id'],error_code='attempts_exhausted')
        job=db.one(c,"SELECT * FROM jobs WHERE attempts<3 AND ((status='queued' AND available_at<=now()) OR (status='running' AND lease_until<now())) ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1")
        if not job: return None
        token=uuid4()
        db.run(c,"UPDATE jobs SET status='running',attempts=attempts+1,lease_token=:t,lease_until=now()+interval '3 minutes' WHERE id=:i",t=token,i=job['id'])
        return dict(job,lease_token=token,attempts=job['attempts']+1)
def finish(engine,job,suggestion=None,error=None,model=None,elapsed=0,retryable=True,error_code=None):
    with engine.begin() as c:
        current=db.one(c,'SELECT *,lease_until>clock_timestamp() AS lease_valid FROM jobs WHERE id=:i FOR UPDATE',i=job['id'])
        if not current or current['status']!='running' or current['lease_token']!=job['lease_token'] or not current['lease_valid']: return False
        db.run(c,'INSERT INTO extraction_attempts(id,job_id,suggestion,error,model,elapsed_ms) VALUES(:i,:j,CAST(:s AS jsonb),:e,:m,:t)',i=uuid4(),j=job['id'],s=json.dumps(suggestion) if suggestion else None,e=error,m=model,t=elapsed)
        status='done' if suggestion is not None else ('failed' if not retryable or current['attempts']>=3 else 'queued')
        if status=='failed' and retryable:
            error='Extraction stopped after three attempts. Manual review is available.'
        db.run(c,"UPDATE jobs SET status=:s,error=:e,lease_token=NULL,lease_until=NULL,available_at=now()+(:delay*interval '1 second') WHERE id=:i",s=status,e=error,delay=30*current['attempts'],i=job['id'])
        db.audit(c,None,'extraction_'+status,job['invoice_id'],attempt=current['attempts'],error_code=error_code)
        return True
def once(engine):
    job=claim(engine)
    if not job: return False
    started=time.monotonic()
    try:
        with engine.connect() as c: inv=db.one(c,'SELECT page_count FROM invoices WHERE id=:i',i=job['invoice_id'])
        suggestion,model=ai.extract(STATE/'documents'/str(job['invoice_id'])/'pages',inv['page_count'])
        finish(engine,job,suggestion=suggestion,model=model,elapsed=int((time.monotonic()-started)*1000))
    except ai.ExtractionError as exc:
        finish(engine,job,error=str(exc),retryable=exc.retryable,error_code=exc.code,elapsed=int((time.monotonic()-started)*1000))
    except Exception:
        # Never persist provider exception strings: they may contain headers or request data.
        finish(engine,job,error='Extraction unavailable. Contact the administrator; manual review is available.',retryable=False,error_code='internal',elapsed=int((time.monotonic()-started)*1000))
    return True
if __name__=='__main__':
    engine=db.engine()
    while True:
        try:
            mailed=notifications.send_once(engine)
            hooked=webhooks.once(engine)
            if not once(engine) and not mailed and not hooked: time.sleep(2)
        except KeyboardInterrupt: break
        except Exception: print('Worker database unavailable; retrying.',flush=True); time.sleep(5)
