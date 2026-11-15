import json
from datetime import date
from uuid import uuid4
import httpx
import pytest
from app import worker,db,ai
from conftest import invoice

def job(engine,seeded):
    ident=invoice(engine,seeded)
    with engine.begin() as c: db.run(c,'INSERT INTO jobs(id,invoice_id) VALUES(:j,:i)',j=uuid4(),i=ident)
    return ident
def test_lease_recovery_fences_old_worker(engine,seeded):
    job(engine,seeded); first=worker.claim(engine); assert worker.claim(engine) is None
    with engine.begin() as c: db.run(c,"UPDATE jobs SET lease_until=now()-interval '1 second'")
    second=worker.claim(engine); assert second['lease_token']!=first['lease_token']
    assert worker.finish(engine,first,suggestion={'old':True}) is False
    assert worker.finish(engine,second,suggestion={'new':True}) is True
    with engine.connect() as c: assert db.run(c,'SELECT count(*) FROM extraction_attempts').scalar_one()==1
def test_maximum_attempts(engine,seeded):
    job(engine,seeded)
    for _ in range(3):
        claimed=worker.claim(engine); assert claimed; worker.finish(engine,claimed,error='Unavailable')
        with engine.begin() as c: db.run(c,"UPDATE jobs SET available_at=now()-interval '1 second'")
    assert worker.claim(engine) is None
    with engine.connect() as c: assert db.run(c,'SELECT status FROM jobs').scalar_one()=='failed'
def suggestion(): return {'supplier_name':'Synthetic','invoice_number':'INV-1','invoice_date':'2026-11-13','due_date':None,'currency':'PKR','lines':[],'tax':None,'discount':None,'declared_total':'25.00','warnings':[]}
def test_ai_json_and_fixed_destination(tmp_path):
    (tmp_path/'1.png').write_bytes(b'fixture')
    def handler(request):
        assert request.url.host=='generativelanguage.googleapis.com' and 'key=' not in str(request.url)
        assert request.headers['x-goog-api-key']=='synthetic-only'
        generation=json.loads(request.content)['generationConfig']
        assert 'temperature' not in generation and 'topP' not in generation
        assert generation['responseJsonSchema']['properties']['tax']['type']==['string','null']
        return httpx.Response(200,json={'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':json.dumps(suggestion())}]}}]})
    result,model=ai.extract(tmp_path,1,{'api_key':'synthetic-only','model':'gemini-test'},httpx.MockTransport(handler));assert result['invoice_number']=='INV-1'
@pytest.mark.parametrize('status',[301,401,429,500])
def test_ai_provider_errors(tmp_path,status):
    (tmp_path/'1.png').write_bytes(b'fixture')
    with pytest.raises(ValueError): ai.extract(tmp_path,1,{'api_key':'synthetic-only','model':'gemini-test'},httpx.MockTransport(lambda r:httpx.Response(status)))
def test_ai_rejects_incomplete_output(tmp_path):
    (tmp_path/'1.png').write_bytes(b'fixture')
    with pytest.raises(ValueError): ai.extract(tmp_path,1,{'api_key':'synthetic-only','model':'gemini-test'},httpx.MockTransport(lambda r:httpx.Response(200,json={'candidates':[{'finishReason':'MAX_TOKENS','content':{'parts':[{'text':'{}'}]}}]})))
