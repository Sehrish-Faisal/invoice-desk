from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from app import db, services, schemas
from conftest import invoice,review_data,sign_in

def prepared(engine,seeded,number='INV-001',qty='20'):
    ident=invoice(engine,seeded); body=schemas.ReviewRequest(**review_data(seeded,number,qty))
    services.save_review(engine,ident,body,seeded['users']['operator']); services.submit(engine,ident,1,seeded['users']['operator'])
    return ident
def decision(): return schemas.Decision(expected_version=2,decision='approve',reason='Source and purchase order checked.')
def test_complete_flow_and_repeat_export(engine,seeded):
    ident=prepared(engine,seeded)
    services.decide(engine,ident,decision(),seeded['users']['approver'])
    a=services.export_invoice(engine,ident,seeded['users']['approver']); b=services.export_invoice(engine,ident,seeded['users']['approver'])
    assert a['id']==b['id'] and a['csv']==b['csv'] and '2510.00' in a['csv']
    with engine.connect() as c:
        assert db.run(c,'SELECT count(*) FROM allocations').scalar_one()==1
        assert db.run(c,'SELECT count(*) FROM exports').scalar_one()==1
        assert db.run(c,"SELECT count(*) FROM audit_events WHERE action='approved'").scalar_one()==1
def test_stale_and_frozen_edit(engine,seeded):
    ident=prepared(engine,seeded)
    with pytest.raises(HTTPException) as exc: services.save_review(engine,ident,schemas.ReviewRequest(**review_data(seeded)),seeded['users']['operator'])
    assert exc.value.status_code==409
    with pytest.raises(HTTPException): services.save_review(engine,ident,schemas.ReviewRequest(**review_data(seeded,version=2)),seeded['users']['operator'])
def test_self_approval_blocked_even_admin(engine,seeded):
    ident=prepared(engine,seeded)
    with engine.begin() as c: db.run(c,'UPDATE invoices SET reviewer_id=:u WHERE id=:i',u=seeded['users']['admin']['id'],i=ident)
    with pytest.raises(HTTPException) as exc: services.decide(engine,ident,decision(),seeded['users']['admin'])
    assert exc.value.status_code==403
def test_duplicate_number_rollback(engine,seeded):
    ident=prepared(engine,seeded); other=invoice(engine,seeded)
    with pytest.raises(IntegrityError): services.save_review(engine,other,schemas.ReviewRequest(**review_data(seeded,number='inv-001')),seeded['users']['operator'])
    with engine.connect() as c: assert db.one(c,'SELECT version FROM invoices WHERE id=:i',i=other)['version']==0
def test_concurrent_approval_never_overbills(engine,seeded):
    a=prepared(engine,seeded,'A','70'); b=prepared(engine,seeded,'B','70')
    def approve(i):
        try: services.decide(engine,i,decision(),seeded['users']['approver']); return 200
        except HTTPException as e: return e.status_code
    with ThreadPoolExecutor(2) as pool: result=list(pool.map(approve,[a,b]))
    assert sorted(result)==[200,409]
    with engine.connect() as c: assert db.run(c,'SELECT sum(quantity) FROM allocations').scalar_one()==70
@pytest.mark.parametrize('change',[{'currency':'USD'},{'declared_total':'5'},{'discount':'3000'}])
def test_invalid_match_blocks_submission(engine,seeded,change):
    data=review_data(seeded);data.update(change); ident=invoice(engine,seeded)
    result=services.save_review(engine,ident,schemas.ReviewRequest(**data),seeded['users']['operator']); assert result['issues']
    with pytest.raises(HTTPException): services.submit(engine,ident,1,seeded['users']['operator'])
@pytest.mark.parametrize('value',['NaN','Infinity','-1','0','1.0001'])
def test_invalid_quantity_rejected(seeded,value):
    data=review_data(seeded);data['lines'][0]['quantity']=value
    with pytest.raises(ValueError): schemas.ReviewRequest(**data)
def test_rounding_and_formula_escaping(seeded):
    data=review_data(seeded);data['lines'][0].update(quantity='1',unit_price='1.005');data['declared_total']='1.01'
    assert schemas.total(schemas.ReviewRequest(**data))==Decimal('1.01')
    assert services.safe_cell(' =SUM(A1)')=="' =SUM(A1)"
def test_rejection_requires_new_saved_review(engine,seeded):
    ident=prepared(engine,seeded); body=schemas.Decision(expected_version=2,decision='reject',reason='Please correct the source reference.')
    services.decide(engine,ident,body,seeded['users']['approver'])
    with pytest.raises(HTTPException): services.submit(engine,ident,3,seeded['users']['operator'])
    services.save_review(engine,ident,schemas.ReviewRequest(**review_data(seeded,version=3)),seeded['users']['operator'])
    services.submit(engine,ident,4,seeded['users']['operator'])
def test_api_roundtrip(client,seeded,engine):
    sign_in(client,seeded); ident=invoice(engine,seeded)
    assert client.put(f'/api/invoices/{ident}/review',json=review_data(seeded)).status_code==200
    assert client.get(f'/api/invoices/{ident}').json()['review']['invoice_number']=='INV-001'
    assert client.post(f'/api/invoices/{ident}/submit',json={'expected_version':1}).status_code==200
    sign_in(client,seeded,'approver')
    assert client.post(f'/api/invoices/{ident}/decision',json=decision().model_dump()).status_code==200
    assert client.post(f'/api/invoices/{ident}/export',json={}).status_code==200
