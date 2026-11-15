import json
from uuid import uuid4
import httpx
import pytest
from app import ai, db, worker
from app.schemas import Suggestion, ReviewRequest
from conftest import invoice, review_data

SETTINGS={'api_key':'synthetic-key','model':'gemini-test'}
def make_job(engine,seeded):
    ident=invoice(engine,seeded)
    with engine.begin() as c:db.run(c,'INSERT INTO jobs(id,invoice_id) VALUES(:j,:i)',j=uuid4(),i=ident)
    return ident
def output(**changes):
    value={'supplier_name':'Synthetic Supplies Ltd','invoice_number':'INV-1','invoice_date':None,'due_date':None,'currency':None,'lines':[{'description':'Item','quantity':None,'unit_price':None,'page':1,'evidence':'Item'}],'tax':None,'discount':None,'declared_total':None,'warnings':['Missing fields require manual review.']}
    return value | changes
def response(value):return httpx.Response(200,json={'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':json.dumps(value)}]}}]})
def test_missing_fields_survive_extraction_but_not_review(tmp_path,seeded):
    (tmp_path/'1.png').write_bytes(b'fixture')
    value,_=ai.extract(tmp_path,1,SETTINGS,httpx.MockTransport(lambda r:response(output())))
    assert value['lines'][0]['quantity'] is None and value['lines'][0]['unit_price'] is None
    body=review_data(seeded);body['lines'][0]['quantity']=None
    with pytest.raises(ValueError):ReviewRequest(**body)
@pytest.mark.parametrize('status,retryable',[(400,False),(401,False),(403,False),(404,False),(302,False),(408,True),(429,True),(500,True),(503,True)])
def test_status_retry_classification(tmp_path,status,retryable):
    (tmp_path/'1.png').write_bytes(b'fixture')
    with pytest.raises(ai.ExtractionError) as exc:
        ai.extract(tmp_path,1,SETTINGS,httpx.MockTransport(lambda r:httpx.Response(status,text='SENSITIVE_SENTINEL')))
    assert exc.value.retryable is retryable
    assert 'SENSITIVE_SENTINEL' not in str(exc.value) and SETTINGS['api_key'] not in str(exc.value)
def test_network_errors_are_safe_and_retryable(tmp_path):
    (tmp_path/'1.png').write_bytes(b'fixture')
    def failed(r):raise httpx.ReadTimeout('SECRET_SENTINEL')
    with pytest.raises(ai.ExtractionError) as exc:ai.extract(tmp_path,1,SETTINGS,httpx.MockTransport(failed))
    assert exc.value.retryable and 'SECRET_SENTINEL' not in str(exc.value)
@pytest.mark.parametrize('value',[{'api_key':'x','model':'../evil'}, {'api_key':'x\nheader: value','model':'gemini-test'},None,{'api_key':123,'model':'gemini-test'}])
def test_configuration_is_checked(value):
    with pytest.raises(ai.ExtractionError):ai.validate_settings(value)
def test_response_size_is_bounded(tmp_path):
    (tmp_path/'1.png').write_bytes(b'fixture')
    with pytest.raises(ai.ExtractionError) as exc:ai.extract(tmp_path,1,SETTINGS,httpx.MockTransport(lambda r:httpx.Response(200,content=b'x'*131073)))
    assert exc.value.code=='response_limit' and not exc.value.retryable
def test_invalid_page_evidence_rejected(tmp_path):
    (tmp_path/'1.png').write_bytes(b'fixture');value=output();value['lines'][0]['page']=2
    with pytest.raises(ai.ExtractionError) as exc:ai.extract(tmp_path,1,SETTINGS,httpx.MockTransport(lambda r:response(value)))
    assert exc.value.code=='invalid_output'
def test_expired_lease_cannot_commit_even_before_reclaim(engine,seeded):
    make_job(engine,seeded);claimed=worker.claim(engine)
    with engine.begin() as c:db.run(c,"UPDATE jobs SET lease_until=now()-interval '1 second'")
    assert worker.finish(engine,claimed,suggestion=output()) is False
    with engine.connect() as c:assert db.run(c,'SELECT count(*) FROM extraction_attempts').scalar_one()==0
@pytest.mark.parametrize('retryable,status',[(False,'failed'),(True,'queued')])
def test_worker_respects_error_category(engine,seeded,monkeypatch,retryable,status):
    make_job(engine,seeded)
    def fail(*args):raise ai.ExtractionError('safe_code','Safe English message.',retryable)
    monkeypatch.setattr(ai,'extract',fail)
    assert worker.once(engine)
    with engine.connect() as c:
        result=db.one(c,'SELECT status,attempts FROM jobs');assert result['status']==status and result['attempts']==1
        assert db.one(c,"SELECT details FROM audit_events WHERE action=:a",a='extraction_'+status)['details']['error_code']=='safe_code'
def test_unexpected_error_never_persists_sensitive_message(engine,seeded,monkeypatch):
    make_job(engine,seeded)
    def fail(*args):raise RuntimeError('SECRET_SENTINEL')
    monkeypatch.setattr(ai,'extract',fail);worker.once(engine)
    with engine.connect() as c:
        row=db.one(c,'SELECT status,error FROM jobs');assert row['status']=='failed' and 'SECRET_SENTINEL' not in row['error']
def test_exhausted_crash_records_one_audit_event(engine,seeded):
    make_job(engine,seeded)
    with engine.begin() as c:db.run(c,"UPDATE jobs SET status='running',attempts=3,lease_until=now()-interval '1 second',lease_token=:t",t=uuid4())
    assert worker.claim(engine) is None and worker.claim(engine) is None
    with engine.connect() as c:
        assert db.run(c,"SELECT count(*) FROM audit_events WHERE action='extraction_failed'").scalar_one()==1
        assert db.one(c,'SELECT lease_token FROM jobs')['lease_token'] is None
